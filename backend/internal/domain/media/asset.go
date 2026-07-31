package media

import "time"

// Asset 表示已归档到本地媒体存储的不可变资源。
type Asset struct {
	ID         string
	Kind       string
	StorageKey string
	MIMEType   string
	SizeBytes  int64
	SHA256     string
	// Origin 标记资产来源。空值表示 v3 正常生成;"legacy-import" 表示
	// 从 v2 迁移导入的历史文件,任务上下文(提示词/账号/模型)已不可恢复。
	Origin    string
	CreatedAt time.Time
}

// OriginLegacyImport 是 v2 历史媒体导入时使用的来源标记。
const OriginLegacyImport = "legacy-import"
