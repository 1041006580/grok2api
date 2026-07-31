package migratev2

import (
	"sort"
	"strings"
)

// 配置逐项映射表:v2 config:user 的点号路径 -> 处置方式。
// 目标是"每个出现过的旧配置都有明确去向",无对应项标 deprecated,
// 需要管理员在 v3 后台手工设置的标 manual,含敏感值的只入加密档案。
var configDispositions = map[string]ConfigMapping{
	"app.app_url":  {Disposition: "manual", V3Target: "设置 → Public API Base URL", Note: "v3 对外地址在管理端设置"},
	"app.api_key":  {Disposition: "sensitive", V3Target: "v3 客户端 Key", Note: "不沿用旧 Key;在 v3 创建 legacy-v2-client 并下发新 g2a_ Key"},
	"app.app_key":  {Disposition: "sensitive", V3Target: "v3 bootstrap 管理员", Note: "不转换为管理员密码;v3 使用新的强密码"},
	"app.webui_enabled": {Disposition: "deprecated", Note: "v2 WebUI 不迁移"},
	"app.webui_key":     {Disposition: "deprecated", Note: "v2 WebUI 不迁移"},

	"proxy.egress.mode":                {Disposition: "manual", V3Target: "设置 → 出口代理", Note: "按语义在 v3 重建 Egress 节点"},
	"proxy.egress.proxy_url":           {Disposition: "sensitive", V3Target: "设置 → 出口代理", Note: "代理地址可能含凭据,只入加密档案"},
	"proxy.egress.proxy_pool":          {Disposition: "sensitive", V3Target: "设置 → 出口代理", Note: "同上"},
	"proxy.egress.resource_proxy_url":  {Disposition: "sensitive", V3Target: "设置 → 出口代理(资源作用域)", Note: "同上"},
	"proxy.egress.resource_proxy_pool": {Disposition: "sensitive", V3Target: "设置 → 出口代理(资源作用域)", Note: "同上"},
	"proxy.egress.skip_ssl_verify":     {Disposition: "manual", V3Target: "设置 → 出口代理", Note: ""},

	"proxy.clearance.mode":             {Disposition: "manual", V3Target: "设置 → Clearance", Note: ""},
	"proxy.clearance.cf_cookies":       {Disposition: "sensitive", V3Target: "设置 → Clearance", Note: "Cloudflare Cookie 只入加密档案"},
	"proxy.clearance.user_agent":       {Disposition: "manual", V3Target: "设置 → Clearance", Note: ""},
	"proxy.clearance.browser":          {Disposition: "manual", V3Target: "设置 → Clearance", Note: ""},
	"proxy.clearance.flaresolverr_url": {Disposition: "manual", V3Target: "设置 → Clearance(FlareSolverr)", Note: ""},
	"proxy.clearance.timeout_sec":      {Disposition: "manual", V3Target: "设置 → Clearance", Note: ""},
	"proxy.clearance.refresh_interval": {Disposition: "manual", V3Target: "设置 → Clearance", Note: ""},

	"xai.keys": {Disposition: "sensitive", V3Target: "xAI Official Provider 账号", Note: "由 import 阶段导入 Provider 账号池"},

	"cache.local.image_max_mb": {Disposition: "manual", V3Target: "设置 → 媒体容量", Note: ""},
	"cache.local.video_max_mb": {Disposition: "manual", V3Target: "设置 → 媒体容量", Note: ""},
}

// 前缀级处置:精确项没有命中时按最长前缀匹配。
var configPrefixDispositions = []struct {
	Prefix  string
	Mapping ConfigMapping
}{
	{"features.", ConfigMapping{Disposition: "deprecated", Note: "v2 功能开关与 v3 行为模型不同,按 v3 默认值运行"}},
	{"logging.", ConfigMapping{Disposition: "deprecated", Note: "v3 日志配置在 config.yaml"}},
	{"retry.", ConfigMapping{Disposition: "deprecated", Note: "v3 重试策略内建"}},
	{"account.", ConfigMapping{Disposition: "deprecated", Note: "v3 账号刷新与选号策略内建/管理端配置"}},
	{"chat.", ConfigMapping{Disposition: "deprecated", Note: "v3 超时策略内建"}},
	{"image.", ConfigMapping{Disposition: "deprecated", Note: "v3 超时策略内建"}},
	{"video.", ConfigMapping{Disposition: "deprecated", Note: "v3 超时策略内建"}},
	{"voice.", ConfigMapping{Disposition: "deprecated", Note: "Voice 首期不迁移"}},
	{"asset.", ConfigMapping{Disposition: "deprecated", Note: "v3 媒体策略内建"}},
	{"nsfw.", ConfigMapping{Disposition: "deprecated", Note: "v3 行为内建"}},
	{"batch.", ConfigMapping{Disposition: "deprecated", Note: "v3 并发策略内建"}},
}

// BuildConfigReport 为 config:user 中每个实际出现的配置项生成映射记录。
// 报告只含 key 与处置方式,不含任何配置值。
func BuildConfigReport(fields map[string]string) []ConfigMapping {
	result := make([]ConfigMapping, 0, len(fields))
	for key := range fields {
		mapping, ok := configDispositions[key]
		if !ok {
			mapping = matchPrefixDisposition(key)
		}
		mapping.V2Key = key
		result = append(result, mapping)
	}
	sort.Slice(result, func(i, j int) bool { return result[i].V2Key < result[j].V2Key })
	return result
}

func matchPrefixDisposition(key string) ConfigMapping {
	best := ConfigMapping{Disposition: "deprecated", Note: "未识别的 v2 配置项,无 v3 对应"}
	bestLen := 0
	for _, entry := range configPrefixDispositions {
		if strings.HasPrefix(key, entry.Prefix) && len(entry.Prefix) > bestLen {
			best = entry.Mapping
			bestLen = len(entry.Prefix)
		}
	}
	return best
}
