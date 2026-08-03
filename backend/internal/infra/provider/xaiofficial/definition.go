// Package xaiofficial 实现官方 api.x.ai 的 API Key Provider。
//
// 与 Build 的 XAI 回退不同,本 Provider 使用管理员导入的官方 API Key,
// 请求为标准 Responses 协议的准透传;Key 加密存储在 Provider Account 体系,
// 通过模型路由(XAI/ 命名空间)显式调用或按模型参与兜底。
package xaiofficial

import (
	"github.com/chenyme/grok2api/backend/internal/domain/account"
	modeldomain "github.com/chenyme/grok2api/backend/internal/domain/model"
	"github.com/chenyme/grok2api/backend/internal/infra/provider"
)

// Definition 声明 xAI Official 的稳定能力边界。
// 首期只覆盖统一对话入口(Responses/Chat/Messages/Stored);图片与视频
// 生成待官方接口适配完成后与实现接口一起声明,不提前放空能力。
func (a *Adapter) Definition() provider.Definition {
	return provider.Definition{
		Provider:          account.ProviderXAIOfficial,
		ModelNamespace:    account.ProviderXAIOfficial.ModelNamespace(),
		ModelCatalog:      provider.ModelCatalogRemote,
		ModelCapabilities: []modeldomain.Capability{modeldomain.CapabilityResponses, modeldomain.CapabilityChat},
		// 官方 API Key 无远端额度窗口;本地窗口只做并发与冷却的载体,
		// 真实限额由官方 429 反馈驱动。
		Quota: provider.QuotaLocalWindow,
		Credential: provider.CredentialSurface{
			AuthType: account.AuthTypeAPIKey, Import: true,
		},
		Conversation: provider.ConversationSurface{
			Responses: true, ChatCompletions: true, Messages: true, StoredResponses: true,
		},
		Inference: provider.InferencePolicy{Usage: provider.UsageUpstream},
	}
}
