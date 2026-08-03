package relational

import (
	"context"
	"path/filepath"
	"strings"
	"testing"

	"github.com/chenyme/grok2api/backend/internal/domain/account"
	clientkeydomain "github.com/chenyme/grok2api/backend/internal/domain/clientkey"
)

// TestInitializeSchemaUpgradesConstraintsForXAIOfficial 验证 console 时代的
// 数据库经 InitializeSchema 后接受 xai_official Provider 与 api_key 凭据,
// 且既有账号数据在表重建过程中完整保留。
func TestInitializeSchemaUpgradesConstraintsForXAIOfficial(t *testing.T) {
	ctx := context.Background()
	database, err := OpenSQLite(ctx, filepath.Join(t.TempDir(), "xai-upgrade.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	if err := database.InitializeSchema(ctx); err != nil {
		t.Fatal(err)
	}
	accountRepository := NewAccountRepository(database)
	created, _, err := accountRepository.UpsertByIdentity(ctx, account.Credential{
		Provider: account.ProviderWeb, AuthType: account.AuthTypeSSO, Name: "existing-web", SourceKey: "sso:existing",
		EncryptedAccessToken: "encrypted", Enabled: true, AuthStatus: account.AuthStatusActive,
	})
	if err != nil {
		t.Fatal(err)
	}

	// 把关键表的约束文本降回 console 时代(不含 xai_official / api_key),
	// 模拟一个已经上线的 v3 库。
	if err := downgradeTableConstraint(ctx, database, "provider_accounts",
		"'grok_build','grok_web','grok_console','xai_official'", "'grok_build','grok_web','grok_console'"); err != nil {
		t.Fatal(err)
	}
	if err := downgradeTableConstraint(ctx, database, "account_credentials",
		"'oauth','sso','api_key'", "'oauth','sso'"); err != nil {
		t.Fatal(err)
	}
	if err := downgradeTableConstraint(ctx, database, "client_keys",
		"provider_scope_mask BETWEEN 1 AND 15", "provider_scope_mask BETWEEN 1 AND 7"); err != nil {
		t.Fatal(err)
	}

	if err := database.InitializeSchema(ctx); err != nil {
		t.Fatal(err)
	}

	if preserved, err := accountRepository.Get(ctx, created.ID); err != nil || preserved.Name != "existing-web" || preserved.EncryptedAccessToken != "encrypted" {
		t.Fatalf("existing account was not preserved: %#v, err=%v", preserved, err)
	}
	for table, marker := range map[string]string{
		"provider_accounts":   "xai_official",
		"account_credentials": "api_key",
		"client_keys":         "BETWEEN 1 AND 15",
	} {
		var sql string
		if err := database.db.WithContext(ctx).Raw("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", table).Scan(&sql).Error; err != nil {
			t.Fatal(err)
		}
		if !strings.Contains(sql, marker) {
			t.Fatalf("table %s was not upgraded: %s", table, sql)
		}
	}

	// 升级后的库必须能写入 api_key 凭据与含 xai_official 的 client key scope。
	imported, _, err := accountRepository.UpsertByIdentity(ctx, account.Credential{
		Provider: account.ProviderXAIOfficial, AuthType: account.AuthTypeAPIKey, Name: "xai-key", SourceKey: "apikey:fingerprint",
		EncryptedAccessToken: "encrypted-key", Enabled: true, AuthStatus: account.AuthStatusActive,
	})
	if err != nil {
		t.Fatalf("importing api_key credential after upgrade failed: %v", err)
	}
	if stored, err := accountRepository.Get(ctx, imported.ID); err != nil || stored.AuthType != account.AuthTypeAPIKey || stored.Provider != account.ProviderXAIOfficial {
		t.Fatalf("api_key credential round-trip failed: %#v, err=%v", stored, err)
	}
	keyRepository := NewClientKeyRepository(database)
	key, err := keyRepository.Create(ctx, clientkeydomain.Key{
		Name: "xai-scope", Prefix: "xaiscope", SecretHash: testSecretHash, EncryptedSecret: testEncryptedToken,
		Enabled: true, ProviderScope: clientkeydomain.ProviderScopeAll, TierScope: clientkeydomain.TierScopeAll,
	})
	if err != nil {
		t.Fatalf("creating client key with xai_official scope failed: %v", err)
	}
	if !key.ProviderScope.Allows(account.ProviderXAIOfficial) {
		t.Fatalf("full provider scope should allow xai_official: %v", key.ProviderScope)
	}
}

// downgradeTableConstraint 用 SQLite 表重建把约束文本替换回旧版本。
func downgradeTableConstraint(ctx context.Context, database *Database, table, current, legacy string) error {
	return database.withSQLiteForeignKeysDisabled(ctx, func() error {
		db := database.db.WithContext(ctx)
		var tableSQL string
		if err := db.Raw("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", table).Scan(&tableSQL).Error; err != nil {
			return err
		}
		legacySQL := strings.Replace(tableSQL, current, legacy, 1)
		legacySQL = strings.Replace(legacySQL, table, table+"_legacy_xai", 1)
		if err := db.Exec(legacySQL).Error; err != nil {
			return err
		}
		if err := db.Exec("INSERT INTO " + table + "_legacy_xai SELECT * FROM " + table).Error; err != nil {
			return err
		}
		if err := db.Exec("DROP TABLE " + table).Error; err != nil {
			return err
		}
		return db.Exec("ALTER TABLE " + table + "_legacy_xai RENAME TO " + table).Error
	})
}
