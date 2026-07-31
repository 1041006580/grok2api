// Package migratev2 实现从 v2(Python/Redis)到 v3 的一次性离线数据迁移。
//
// 数据流:v2 Redis / 媒体清单 --export--> 加密档案 + 明文报告
// --import--> v3 PostgreSQL/SQLite 与本地媒体目录 --verify--> 对账报告。
// 全程只读 v2,不回写;可重复执行,按稳定来源标识保持幂等。
package migratev2

import "time"

// V2Account 是 v2 Redis accounts:record:<token> 反序列化后的账号记录。
// 字段语义与 v2 的 AccountRecord 一致;空字符串一律解析为零值。
type V2Account struct {
	Token     string   `json:"token"`
	Pool      string   `json:"pool"`   // basic | super | heavy
	Status    string   `json:"status"` // active | cooling | expired | disabled
	Tags      []string `json:"tags,omitempty"`
	CreatedAt int64    `json:"created_at,omitempty"` // 毫秒时间戳,0 表示缺失
	UpdatedAt int64    `json:"updated_at,omitempty"`
	LastUseAt int64    `json:"last_use_at,omitempty"`
	DeletedAt int64    `json:"deleted_at,omitempty"`

	// 以下仅入档案归档,不写入 v3 活动状态:v2 的额度窗口、冷却与失败
	// 语义和 v3 的 Provider 健康模型不同,导入后由 v3 自行重新同步。
	Quota       map[string]string `json:"quota,omitempty"` // 窗口名 -> 原始 JSON 文本
	UsageUse    int64             `json:"usage_use_count,omitempty"`
	UsageFail   int64             `json:"usage_fail_count,omitempty"`
	UsageSync   int64             `json:"usage_sync_count,omitempty"`
	LastFailAt  int64             `json:"last_fail_at,omitempty"`
	LastFail    string            `json:"last_fail_reason,omitempty"`
	LastSyncAt  int64             `json:"last_sync_at,omitempty"`
	StateReason string            `json:"state_reason,omitempty"`
	Ext         string            `json:"ext,omitempty"` // 原始 JSON 文本(冷却/403 计数等)
	Revision    int64             `json:"revision,omitempty"`
}

// IsNSFW 判断 v2 账号是否带 nsfw 标签。
func (a V2Account) IsNSFW() bool {
	for _, tag := range a.Tags {
		if tag == "nsfw" {
			return true
		}
	}
	return false
}

// V2XAIKey 是 v2 配置 xai.keys 数组中的一条官方 xAI API Key 记录。
type V2XAIKey struct {
	ID      string `json:"id"`
	Key     string `json:"key"`
	Name    string `json:"name,omitempty"`
	Enabled bool   `json:"enabled"`
}

// MediaEntry 是待迁移媒体文件清单中的一项。
type MediaEntry struct {
	Kind      string `json:"kind"` // image | video
	FileName  string `json:"file_name"`
	SizeBytes int64  `json:"size_bytes,omitempty"`  // 来自 SQLite 索引,可能缺失
	CreatedNS int64  `json:"created_at_ns,omitempty"`
}

// AssetID 返回媒体资产 ID(文件名去扩展名)。
func (e MediaEntry) AssetID() string {
	name := e.FileName
	for i := len(name) - 1; i >= 0; i-- {
		if name[i] == '.' {
			return name[:i]
		}
	}
	return name
}

// Archive 是 export 产出、import 消费的完整迁移档案。整体加密落盘,
// 明文中含 SSO Token 与官方 xAI Key,严禁写入日志或未加密文件。
type Archive struct {
	FormatVersion int          `json:"format_version"`
	ExportedAtMS  int64        `json:"exported_at_ms"`
	Accounts      []V2Account  `json:"accounts"`
	XAIKeys       []V2XAIKey   `json:"xai_keys,omitempty"`
	Config        map[string]string `json:"config,omitempty"` // config:user 原始 field -> JSON 文本
	Media         []MediaEntry `json:"media,omitempty"`
}

// ArchiveFormatVersion 是当前档案格式版本。
const ArchiveFormatVersion = 1

// ConfigMapping 是配置逐项映射报告中的一条记录。敏感值不出现在报告中。
type ConfigMapping struct {
	V2Key      string `json:"v2_key"`
	Disposition string `json:"disposition"` // mapped | manual | deprecated | sensitive
	V3Target   string `json:"v3_target,omitempty"`
	Note       string `json:"note,omitempty"`
}

// Report 是 export/import/verify 各阶段共用的非敏感执行报告。
type Report struct {
	Stage        string          `json:"stage"`
	StartedAtMS  int64           `json:"started_at_ms"`
	FinishedAtMS int64           `json:"finished_at_ms"`
	Accounts     ReportCounts    `json:"accounts"`
	XAIKeys      ReportCounts    `json:"xai_keys"`
	Media        ReportCounts    `json:"media"`
	Config       []ConfigMapping `json:"config,omitempty"`
	Problems     []string        `json:"problems,omitempty"`
}

// ReportCounts 汇总一类对象的处理数量。
type ReportCounts struct {
	Found    int `json:"found"`
	Imported int `json:"imported"`
	Updated  int `json:"updated"`
	Skipped  int `json:"skipped"`
	Failed   int `json:"failed"`
}

func nowMS() int64 { return time.Now().UnixMilli() }
