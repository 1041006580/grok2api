package migratev2

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"

	"github.com/chenyme/grok2api/backend/internal/domain/account"
	"github.com/chenyme/grok2api/backend/internal/infra/persistence/relational"
	"github.com/chenyme/grok2api/backend/internal/infra/security"
)

// fakeReader 以 v2 真实 Redis 布局构造测试数据。
type fakeReader struct {
	sets   map[string][]string
	hashes map[string]map[string]string
}

func (f fakeReader) SMembers(_ context.Context, key string) ([]string, error) {
	return f.sets[key], nil
}

func (f fakeReader) HGetAll(_ context.Context, key string) (map[string]string, error) {
	return f.hashes[key], nil
}

func newTestCipher(t *testing.T) *security.Cipher {
	t.Helper()
	key := make([]byte, 32)
	if _, err := rand.Read(key); err != nil {
		t.Fatal(err)
	}
	cipher, err := security.NewCipher(base64.StdEncoding.EncodeToString(key))
	if err != nil {
		t.Fatal(err)
	}
	return cipher
}

func fixtureSource() *Source {
	return &Source{reader: fakeReader{
		sets: map[string][]string{
			v2KeyPoolPrefix + "super": {"token-super-1"},
			v2KeyPoolPrefix + "basic": {"token-basic-1", "token-deleted"},
		},
		hashes: map[string]map[string]string{
			v2KeyAccountRecord + "token-super-1": {
				"pool": "super", "status": "active", "tags": `["nsfw"]`,
				"created_at": "1700000000000", "updated_at": "1700000100000",
				"last_use_at": "1700000200000", "deleted_at": "",
				"quota_auto": `{"remaining":50,"total":50,"window_seconds":7200,"reset_at":0,"synced_at":0,"source":1}`,
				"quota_heavy": "{}", "usage_use_count": "12", "ext": `{"forbidden_strikes":1}`,
				"revision": "7",
			},
			v2KeyAccountRecord + "token-basic-1": {
				"pool": "basic", "status": "disabled", "tags": "", "deleted_at": "",
				"updated_at": "1700000000000",
			},
			v2KeyAccountRecord + "token-deleted": {
				"pool": "basic", "status": "active", "deleted_at": "1700000300000",
			},
			v2KeyConfigUser: {
				"app.api_key":       `"old-api-key"`,
				"features.stream":   "true",
				"proxy.egress.mode": `"single"`,
				v2ConfigXAIKeys:     `[{"id":"k1","key":"xai-abc","name":"main","enabled":true},{"id":"k2","key":"","enabled":false}]`,
			},
		},
	}}
}

func TestLoadAccountsParsesAndFiltersV2Records(t *testing.T) {
	accounts, skipped, err := fixtureSource().LoadAccounts(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(accounts) != 2 || skipped != 1 {
		t.Fatalf("accounts=%d skipped=%d", len(accounts), skipped)
	}
	var superAccount V2Account
	for _, value := range accounts {
		if value.Pool == "super" {
			superAccount = value
		}
	}
	if superAccount.Token != "token-super-1" || !superAccount.IsNSFW() {
		t.Fatalf("super 账号解析错误: %+v", superAccount)
	}
	if superAccount.UsageUse != 12 || superAccount.Revision != 7 {
		t.Fatalf("数字字段解析错误: %+v", superAccount)
	}
	if _, ok := superAccount.Quota["auto"]; !ok {
		t.Fatal("quota_auto 应保留")
	}
	if _, ok := superAccount.Quota["heavy"]; ok {
		t.Fatal(`quota_heavy 为 "{}" 时应视为缺失`)
	}
}

func TestLoadConfigParsesXAIKeys(t *testing.T) {
	fields, keys, err := fixtureSource().LoadConfig(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(keys) != 1 || keys[0].Key != "xai-abc" {
		t.Fatalf("xai.keys 解析错误: %+v", keys)
	}
	if fields["app.api_key"] != `"old-api-key"` {
		t.Fatal("config:user 原始 field 应原样保留")
	}
}

func TestBuildConfigReportCoversEveryKey(t *testing.T) {
	fields, _, err := fixtureSource().LoadConfig(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	report := BuildConfigReport(fields)
	if len(report) != len(fields) {
		t.Fatalf("每个配置项都必须出现在报告中: report=%d fields=%d", len(report), len(fields))
	}
	byKey := make(map[string]ConfigMapping)
	for _, item := range report {
		if item.Disposition == "" {
			t.Fatalf("配置项 %s 缺少处置方式", item.V2Key)
		}
		byKey[item.V2Key] = item
	}
	if byKey["app.api_key"].Disposition != "sensitive" {
		t.Fatal("app.api_key 必须标记 sensitive")
	}
	if byKey["features.stream"].Disposition != "deprecated" {
		t.Fatal("features.* 应按前缀标记 deprecated")
	}
}

func TestArchiveRoundTripRequiresSameKey(t *testing.T) {
	cipher := newTestCipher(t)
	path := filepath.Join(t.TempDir(), "archive.enc")
	archive := Archive{FormatVersion: ArchiveFormatVersion, Accounts: []V2Account{{Token: "secret-token", Pool: "basic"}}}
	if err := WriteArchive(path, cipher, archive); err != nil {
		t.Fatal(err)
	}
	restored, err := ReadArchive(path, cipher)
	if err != nil {
		t.Fatal(err)
	}
	if len(restored.Accounts) != 1 || restored.Accounts[0].Token != "secret-token" {
		t.Fatalf("档案往返失败: %+v", restored)
	}
	if _, err := ReadArchive(path, newTestCipher(t)); err == nil {
		t.Fatal("不同密钥必须无法解密档案")
	}
}

func TestImportAccountsIsIdempotentAndVerifyPasses(t *testing.T) {
	ctx := context.Background()
	database, err := relational.OpenSQLite(ctx, filepath.Join(t.TempDir(), "v3.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	if err := database.InitializeSchema(ctx); err != nil {
		t.Fatal(err)
	}
	cipher := newTestCipher(t)
	accounts, _, err := fixtureSource().LoadAccounts(ctx)
	if err != nil {
		t.Fatal(err)
	}
	archive := Archive{FormatVersion: ArchiveFormatVersion, Accounts: accounts}

	importer, err := NewImporter(database, cipher, ImportOptions{})
	if err != nil {
		t.Fatal(err)
	}
	report, err := importer.Run(ctx, archive, ImportOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if report.Accounts.Imported != 2 || report.Accounts.Failed != 0 {
		t.Fatalf("首轮导入: %+v", report.Accounts)
	}

	// 幂等:重跑必须全部命中更新而不是新建。
	report, err = importer.Run(ctx, archive, ImportOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if report.Accounts.Imported != 0 || report.Accounts.Updated != 2 {
		t.Fatalf("重跑应幂等: %+v", report.Accounts)
	}

	// 落库形态:SourceKey/tier/enabled/加密均符合 v3 Web 惯例。
	repo := relational.NewAccountRepository(database)
	stored, _, err := repo.ListProviderAccountBatch(ctx, account.ProviderWeb, 0, 100)
	if err != nil {
		t.Fatal(err)
	}
	if len(stored) != 2 {
		t.Fatalf("应有 2 个账号,得到 %d", len(stored))
	}
	expectedSourceKey := "sso:" + security.HashToken("token-super-1")
	var superStored account.Credential
	for _, value := range stored {
		if value.SourceKey == expectedSourceKey {
			superStored = value
		}
	}
	if superStored.ID == 0 {
		t.Fatal("super 账号未按 SourceKey 落库")
	}
	if superStored.WebTier != account.WebTierSuper || superStored.AuthType != account.AuthTypeSSO || !superStored.Enabled {
		t.Fatalf("super 账号映射错误: %+v", superStored)
	}
	if superStored.WebNSFWEnabledAt == nil {
		t.Fatal("nsfw 标签应映射为 WebNSFWEnabledAt")
	}
	if superStored.EncryptedAccessToken == "" || strings.Contains(superStored.EncryptedAccessToken, "token-super-1") {
		t.Fatal("凭据必须加密落库")
	}

	// verify 对账全绿。
	verifyReport, err := Verify(ctx, database, archive, "")
	if err != nil {
		t.Fatal(err)
	}
	if len(verifyReport.Problems) != 0 || verifyReport.Accounts.Imported != 2 {
		t.Fatalf("verify 应通过: %+v", verifyReport)
	}
}

func TestMediaImportFetchesArchivesAndVerifies(t *testing.T) {
	ctx := context.Background()
	imageBody := []byte("fake-jpeg-bytes")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/v1/files/image" && r.URL.Query().Get("id") == "0123456789abcdef0123" {
			_, _ = w.Write(imageBody)
			return
		}
		http.NotFound(w, r)
	}))
	defer server.Close()

	database, err := relational.OpenSQLite(ctx, filepath.Join(t.TempDir(), "v3.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	if err := database.InitializeSchema(ctx); err != nil {
		t.Fatal(err)
	}
	mediaRoot := t.TempDir()
	options := ImportOptions{V2BaseURL: server.URL, MediaRoot: mediaRoot}
	importer, err := NewImporter(database, newTestCipher(t), options)
	if err != nil {
		t.Fatal(err)
	}
	archive := Archive{FormatVersion: ArchiveFormatVersion, Media: []MediaEntry{
		{Kind: "image", FileName: "0123456789abcdef0123.jpg"},
		{Kind: "video", FileName: "unsupported.mkv"},
	}}
	report, err := importer.Run(ctx, archive, options)
	if err != nil {
		t.Fatal(err)
	}
	if report.Media.Imported != 1 || report.Media.Skipped != 1 {
		t.Fatalf("媒体导入: %+v", report.Media)
	}

	// 幂等重跑。
	report, err = importer.Run(ctx, archive, options)
	if err != nil {
		t.Fatal(err)
	}
	if report.Media.Imported != 0 || report.Media.Skipped != 2 {
		t.Fatalf("媒体重跑应幂等: %+v", report.Media)
	}

	// verify:mkv 未导入应被如实报告,jpg 的 SHA-256 必须一致。
	verifyReport, err := Verify(ctx, database, archive, mediaRoot)
	if err != nil {
		t.Fatal(err)
	}
	if verifyReport.Media.Imported != 1 || verifyReport.Media.Failed != 1 {
		t.Fatalf("媒体 verify: %+v", verifyReport.Media)
	}
}

func TestLegacyAssetID(t *testing.T) {
	if got := LegacyAssetID("0123456789ABCDEF0123.jpg"); got != "0123456789abcdef0123" {
		t.Fatalf("合规 ID 应小写保留: %s", got)
	}
	short := LegacyAssetID("abc.png")
	if !strings.HasPrefix(short, "legacy-") || len(short) < 16 {
		t.Fatalf("短 ID 应退化为 legacy- 哈希: %s", short)
	}
	if short != LegacyAssetID("abc.png") {
		t.Fatal("退化 ID 必须稳定")
	}
}
