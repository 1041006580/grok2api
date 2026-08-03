package xaiofficial

import (
	"bytes"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/chenyme/grok2api/backend/internal/domain/account"
	"github.com/chenyme/grok2api/backend/internal/infra/provider"
	"github.com/chenyme/grok2api/backend/internal/infra/security"
)

const (
	maxImportAccounts = 10000
	maxAPIKeyBytes    = 4 << 10
)

type importDocument struct {
	Provider string        `json:"provider"`
	Accounts []importEntry `json:"accounts"`
}

type importEntry struct {
	Name   string `json:"name"`
	APIKey string `json:"api_key"`
	Key    string `json:"key"`
}

// parseImportedCredentials 解析 xAI Official API Key 导入文件。
// 支持版本化 JSON 文档与逐行纯文本 Key;Key 指纹作为稳定 SourceKey,
// 重复 Key 幂等合并。
func parseImportedCredentials(data []byte) ([]provider.CredentialSeed, error) {
	data = bytes.TrimPrefix(data, []byte{0xef, 0xbb, 0xbf})
	trimmed := strings.TrimSpace(string(data))
	if trimmed == "" {
		return nil, fmt.Errorf("文件中没有 xAI API Key")
	}
	if !strings.HasPrefix(trimmed, "{") {
		return parsePlainTextCredentials(trimmed)
	}
	entries, err := provider.DecodeCredentialJSONEntries[importEntry](data, string(account.ProviderXAIOfficial), maxImportAccounts)
	if err != nil {
		return nil, fmt.Errorf("解析 xAI API Key JSON: %w", err)
	}
	if len(entries) == 0 {
		return nil, fmt.Errorf("文件中没有 xAI API Key")
	}
	seen := make(map[string]struct{}, len(entries))
	result := make([]provider.CredentialSeed, 0, len(entries))
	for index, entry := range entries {
		key := sanitizeAPIKey(firstNonEmpty(entry.APIKey, entry.Key))
		if key == "" {
			return nil, fmt.Errorf("第 %d 条记录缺少 api_key", index+1)
		}
		if len(key) > maxAPIKeyBytes {
			return nil, fmt.Errorf("第 %d 条记录的 api_key 超过 4 KiB", index+1)
		}
		if _, exists := seen[key]; exists {
			continue
		}
		seen[key] = struct{}{}
		result = append(result, credentialSeed(strings.TrimSpace(entry.Name), key))
	}
	return result, nil
}

func parsePlainTextCredentials(value string) ([]provider.CredentialSeed, error) {
	lines := strings.Split(value, "\n")
	seen := make(map[string]struct{}, len(lines))
	result := make([]provider.CredentialSeed, 0, len(lines))
	for index, line := range lines {
		key := sanitizeAPIKey(line)
		if key == "" {
			continue
		}
		if len(key) > maxAPIKeyBytes {
			return nil, fmt.Errorf("第 %d 行的 api_key 超过 4 KiB", index+1)
		}
		if _, exists := seen[key]; exists {
			continue
		}
		seen[key] = struct{}{}
		result = append(result, credentialSeed("", key))
		if len(result) > maxImportAccounts {
			return nil, provider.ErrCredentialLimit
		}
	}
	if len(result) == 0 {
		return nil, fmt.Errorf("文本中没有有效的 xAI API Key")
	}
	return result, nil
}

func credentialSeed(name, key string) provider.CredentialSeed {
	fingerprint := security.HashToken(key)
	if name == "" {
		name = "xAI Key " + fingerprint[:8]
	}
	return provider.CredentialSeed{
		Provider: account.ProviderXAIOfficial, AuthType: account.AuthTypeAPIKey,
		Name: name, SourceKey: "apikey:" + fingerprint, AccessToken: key,
	}
}

func marshalCredentials(values []provider.CredentialSeed) ([]byte, error) {
	document := importDocument{Provider: string(account.ProviderXAIOfficial), Accounts: make([]importEntry, 0, len(values))}
	for _, value := range values {
		document.Accounts = append(document.Accounts, importEntry{Name: value.Name, APIKey: value.AccessToken})
	}
	data, err := json.MarshalIndent(document, "", "  ")
	if err != nil {
		return nil, err
	}
	return append(data, '\n'), nil
}

// sanitizeAPIKey 清理粘贴噪声;官方 Key 是 ASCII 字符串,常见前缀为 xai-。
func sanitizeAPIKey(value string) string {
	value = strings.TrimSpace(value)
	value = strings.TrimPrefix(value, "Bearer ")
	value = strings.TrimPrefix(value, "bearer ")
	var builder strings.Builder
	for _, r := range value {
		if r > 0x20 && r < 0x7f {
			builder.WriteRune(r)
		}
	}
	return builder.String()
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			return value
		}
	}
	return ""
}
