package migratev2

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"strings"

	"github.com/chenyme/grok2api/backend/internal/domain/account"
	inframedia "github.com/chenyme/grok2api/backend/internal/infra/media"
	"github.com/chenyme/grok2api/backend/internal/infra/persistence/relational"
	"github.com/chenyme/grok2api/backend/internal/infra/security"
	"github.com/chenyme/grok2api/backend/internal/repository"
)

// Verify 用档案对 v3 数据做只读对账:账号按 Token 指纹逐个核对,
// 媒体按资产记录与磁盘文件双向校验 SHA-256。任何缺失都进 Problems。
func Verify(ctx context.Context, database *relational.Database, archive Archive, mediaRoot string) (Report, error) {
	report := Report{Stage: "verify", StartedAtMS: nowMS()}
	accounts := relational.NewAccountRepository(database)

	stored, err := loadAllWebAccounts(ctx, accounts)
	if err != nil {
		return report, err
	}
	bySourceKey := make(map[string]account.Credential, len(stored))
	for _, value := range stored {
		bySourceKey[value.SourceKey] = value
	}

	report.Accounts.Found = len(archive.Accounts)
	for _, v2 := range archive.Accounts {
		sourceKey := "sso:" + security.HashToken(strings.TrimSpace(v2.Token))
		value, ok := bySourceKey[sourceKey]
		if !ok {
			report.Accounts.Failed++
			report.Problems = append(report.Problems, fmt.Sprintf("账号 %s 未在 v3 中找到", v2Fingerprint(v2.Token)))
			continue
		}
		expectedTier, _ := webTierFromPool(v2.Pool)
		if value.WebTier != expectedTier {
			report.Problems = append(report.Problems,
				fmt.Sprintf("账号 %s tier 不一致: v2=%s v3=%s", v2Fingerprint(v2.Token), v2.Pool, value.WebTier))
		}
		if enabled := v2.Status != "disabled"; value.Enabled != enabled {
			report.Problems = append(report.Problems,
				fmt.Sprintf("账号 %s 启停不一致: v2=%s v3.enabled=%t", v2Fingerprint(v2.Token), v2.Status, value.Enabled))
		}
		report.Accounts.Imported++
	}

	if err := verifyMedia(ctx, database, archive.Media, mediaRoot, &report); err != nil {
		return report, err
	}
	report.FinishedAtMS = nowMS()
	return report, nil
}

func loadAllWebAccounts(ctx context.Context, accounts *relational.AccountRepository) ([]account.Credential, error) {
	var result []account.Credential
	afterID := uint64(0)
	for {
		batch, _, err := accounts.ListProviderAccountBatch(ctx, account.ProviderWeb, afterID, 500)
		if err != nil {
			return nil, fmt.Errorf("读取 v3 账号: %w", err)
		}
		if len(batch) == 0 {
			return result, nil
		}
		result = append(result, batch...)
		afterID = batch[len(batch)-1].ID
	}
}

func verifyMedia(ctx context.Context, database *relational.Database, entries []MediaEntry, mediaRoot string, report *Report) error {
	report.Media.Found = len(entries)
	if len(entries) == 0 {
		return nil
	}
	assets := relational.NewMediaAssetRepository(database)
	var store *inframedia.LocalStore
	if strings.TrimSpace(mediaRoot) != "" {
		opened, err := inframedia.NewLocalStore(mediaRoot)
		if err != nil {
			return fmt.Errorf("打开 v3 媒体目录: %w", err)
		}
		store = opened
	}
	for _, entry := range entries {
		if err := ctx.Err(); err != nil {
			return err
		}
		assetID := LegacyAssetID(entry.FileName)
		asset, err := assets.GetMediaAsset(ctx, assetID)
		if err == repository.ErrNotFound {
			report.Media.Failed++
			report.Problems = append(report.Problems, fmt.Sprintf("媒体 %s 未在 v3 中找到", entry.FileName))
			continue
		} else if err != nil {
			return fmt.Errorf("查询媒体资产 %s: %w", assetID, err)
		}
		if store != nil {
			sum, err := hashStoredObject(ctx, store, asset.StorageKey)
			if err != nil {
				report.Media.Failed++
				report.Problems = append(report.Problems, fmt.Sprintf("媒体 %s 文件不可读: %v", entry.FileName, err))
				continue
			}
			if sum != asset.SHA256 {
				report.Media.Failed++
				report.Problems = append(report.Problems, fmt.Sprintf("媒体 %s SHA-256 不一致", entry.FileName))
				continue
			}
		}
		report.Media.Imported++
	}
	return nil
}

func hashStoredObject(ctx context.Context, store *inframedia.LocalStore, storageKey string) (string, error) {
	reader, err := store.Open(ctx, storageKey)
	if err != nil {
		return "", err
	}
	defer func() { _ = reader.Close() }()
	digest := sha256.New()
	if _, err := io.Copy(digest, reader); err != nil {
		return "", err
	}
	return hex.EncodeToString(digest.Sum(nil)), nil
}
