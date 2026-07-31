package migratev2

import (
	"encoding/json"
	"fmt"
	"os"

	"github.com/chenyme/grok2api/backend/internal/infra/security"
)

// WriteArchive 将档案 JSON 序列化后用凭据加密密钥整体加密落盘(0600)。
// 档案含 SSO Token 与官方 xAI Key 明文,绝不允许未加密写出。
func WriteArchive(path string, cipher *security.Cipher, value Archive) error {
	data, err := json.Marshal(value)
	if err != nil {
		return fmt.Errorf("序列化迁移档案: %w", err)
	}
	sealed, err := cipher.Encrypt(string(data))
	if err != nil {
		return fmt.Errorf("加密迁移档案: %w", err)
	}
	if err := os.WriteFile(path, []byte(sealed), 0o600); err != nil {
		return fmt.Errorf("写入迁移档案: %w", err)
	}
	return nil
}

// ReadArchive 解密并解析迁移档案。
func ReadArchive(path string, cipher *security.Cipher) (Archive, error) {
	sealed, err := os.ReadFile(path)
	if err != nil {
		return Archive{}, fmt.Errorf("读取迁移档案: %w", err)
	}
	plain, err := cipher.Decrypt(string(sealed))
	if err != nil {
		return Archive{}, fmt.Errorf("解密迁移档案(密钥必须与导出时一致): %w", err)
	}
	var value Archive
	if err := json.Unmarshal([]byte(plain), &value); err != nil {
		return Archive{}, fmt.Errorf("解析迁移档案: %w", err)
	}
	if value.FormatVersion != ArchiveFormatVersion {
		return Archive{}, fmt.Errorf("迁移档案格式版本 %d 不受支持(期望 %d)", value.FormatVersion, ArchiveFormatVersion)
	}
	return value, nil
}

// WriteReport 将非敏感报告写为明文 JSON。调用方保证报告中无凭据。
func WriteReport(path string, value Report) error {
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, append(data, '\n'), 0o644)
}
