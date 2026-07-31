package migratev2

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/chenyme/grok2api/backend/internal/domain/account"
	"github.com/chenyme/grok2api/backend/internal/infra/persistence/relational"
	"github.com/chenyme/grok2api/backend/internal/infra/security"
)

// ImportOptions 控制 import 阶段行为。
type ImportOptions struct {
	DryRun bool
	// V2BaseURL 是仍在运行的 v2 服务地址,用于按清单拉取媒体文件;
	// 为空时跳过媒体导入,只导账号。
	V2BaseURL string
	// MediaRoot 是 v3 media.local.path;媒体导入时必填。
	MediaRoot string
}

// Importer 将解密后的档案写入 v3。所有写入均通过 v3 仓储层,凭据经
// v3 加密机制落库;重复执行按 SourceKey/资产 ID 幂等。
type Importer struct {
	accounts *relational.AccountRepository
	cipher   *security.Cipher
	media    *mediaImporter
}

// NewImporter 组装导入器。database 必须已完成 InitializeSchema。
func NewImporter(database *relational.Database, cipher *security.Cipher, options ImportOptions) (*Importer, error) {
	importer := &Importer{accounts: relational.NewAccountRepository(database), cipher: cipher}
	if options.V2BaseURL != "" {
		media, err := newMediaImporter(database, options)
		if err != nil {
			return nil, err
		}
		importer.media = media
	}
	return importer, nil
}

// Run 执行档案导入并返回报告。dry-run 只统计不写入。
func (im *Importer) Run(ctx context.Context, archive Archive, options ImportOptions) (Report, error) {
	report := Report{Stage: "import", StartedAtMS: nowMS()}
	report.Config = BuildConfigReport(archive.Config)

	if err := im.importAccounts(ctx, archive.Accounts, options.DryRun, &report); err != nil {
		return report, err
	}
	// 官方 xAI Key 的导入依赖 xai_official Provider;在 Provider 落地前
	// Key 保留在加密档案中,这里只如实计数,绝不静默丢弃。
	report.XAIKeys.Found = len(archive.XAIKeys)
	if len(archive.XAIKeys) > 0 {
		report.XAIKeys.Skipped = len(archive.XAIKeys)
		report.Problems = append(report.Problems,
			fmt.Sprintf("%d 个官方 xAI Key 暂未导入(等待 xai_official Provider),保留在加密档案中", len(archive.XAIKeys)))
	}
	if im.media != nil {
		if err := im.media.run(ctx, archive.Media, options.DryRun, &report); err != nil {
			return report, err
		}
	} else {
		report.Media.Found = len(archive.Media)
		report.Media.Skipped = len(archive.Media)
		if len(archive.Media) > 0 {
			report.Problems = append(report.Problems, "未提供 --v2-base-url,媒体文件未导入")
		}
	}
	report.FinishedAtMS = nowMS()
	return report, nil
}

func (im *Importer) importAccounts(ctx context.Context, accounts []V2Account, dryRun bool, report *Report) error {
	report.Accounts.Found = len(accounts)
	values := make([]account.Credential, 0, len(accounts))
	for _, v2 := range accounts {
		value, err := im.credentialFromV2(v2)
		if err != nil {
			report.Accounts.Failed++
			report.Problems = append(report.Problems, fmt.Sprintf("账号 %s: %v", v2Fingerprint(v2.Token), err))
			continue
		}
		values = append(values, value)
	}
	if dryRun {
		report.Accounts.Skipped = len(values)
		return nil
	}
	results, err := im.accounts.UpsertManyByIdentity(ctx, values)
	if err != nil {
		return fmt.Errorf("写入 v3 账号: %w", err)
	}
	for index, result := range results {
		if result.Created {
			report.Accounts.Imported++
		} else {
			report.Accounts.Updated++
		}
		// 上游 upsert 有意在新建时强制启用;v2 中已停用的账号需要按
		// 管理端相同的更新路径回写,保持迁移前后的启停状态一致。
		if index < len(values) && !values[index].Enabled {
			if err := im.disableAccount(ctx, result.ID); err != nil {
				report.Problems = append(report.Problems, fmt.Sprintf("账号 %s 回写停用状态失败: %v", values[index].SourceKey[:16], err))
			}
		}
	}
	return nil
}

func (im *Importer) disableAccount(ctx context.Context, id uint64) error {
	stored, err := im.accounts.Get(ctx, id)
	if err != nil {
		return err
	}
	if !stored.Enabled {
		return nil
	}
	stored.Enabled = false
	_, err = im.accounts.Update(ctx, stored)
	return err
}

// credentialFromV2 把 v2 账号映射为 v3 grok_web 凭据。构造规则与
// v3 账号服务 credentialFromSeed 的 Web 分支保持一致:SourceKey 与
// EgressIdentity 都由 Token 哈希派生,保证与后续正常导入互相幂等。
// v2 的额度、冷却与失败状态不写入 v3,导入后由 v3 重新同步。
func (im *Importer) credentialFromV2(v2 V2Account) (account.Credential, error) {
	token := strings.TrimSpace(v2.Token)
	if token == "" {
		return account.Credential{}, fmt.Errorf("空 token")
	}
	tier, err := webTierFromPool(v2.Pool)
	if err != nil {
		return account.Credential{}, err
	}
	encrypted, err := im.cipher.Encrypt(token)
	if err != nil {
		return account.Credential{}, fmt.Errorf("加密凭据: %w", err)
	}
	hash := security.HashToken(token)
	value := account.Credential{
		Provider:             account.ProviderWeb,
		AuthType:             account.AuthTypeSSO,
		WebTier:              tier,
		Name:                 "Grok Web " + hash[:8],
		SourceKey:            "sso:" + hash,
		EncryptedAccessToken: encrypted,
		EgressIdentity:       "sso_" + hash[:32],
		Enabled:              v2.Status != "disabled",
		AuthStatus:           account.AuthStatusActive,
		Priority:             account.DefaultPriority,
		MaxConcurrent:        account.DefaultMaxConcurrent,
		MinimumRemaining:     account.DefaultMinimumRemaining,
	}
	// v2 的 nsfw 标签代表上游已确认开启;缺少精确时间,取 v2 记录的
	// 最后更新时间作为锚点,避免 v3 重复执行开启流程。
	if v2.IsNSFW() {
		at := msTime(v2.UpdatedAt)
		if at == nil {
			now := time.Now()
			at = &now
		}
		value.WebNSFWEnabledAt = at
	}
	if used := msTime(v2.LastUseAt); used != nil {
		value.LastUsedAt = used
	}
	return value, nil
}

func webTierFromPool(pool string) (account.WebTier, error) {
	switch strings.ToLower(strings.TrimSpace(pool)) {
	case "basic":
		return account.WebTierBasic, nil
	case "super":
		return account.WebTierSuper, nil
	case "heavy":
		return account.WebTierHeavy, nil
	default:
		return "", fmt.Errorf("未知 v2 账号池 %q", pool)
	}
}

func msTime(ms int64) *time.Time {
	if ms <= 0 {
		return nil
	}
	value := time.UnixMilli(ms)
	return &value
}

// v2Fingerprint 返回用于日志与报告的不可逆短指纹,绝不输出 Token 本身。
func v2Fingerprint(token string) string {
	hash := security.HashToken(strings.TrimSpace(token))
	if len(hash) < 12 {
		return "invalid"
	}
	return hash[:12]
}
