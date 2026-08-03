package xaiofficial

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/chenyme/grok2api/backend/internal/domain/account"
	egressdomain "github.com/chenyme/grok2api/backend/internal/domain/egress"
	infraegress "github.com/chenyme/grok2api/backend/internal/infra/egress"
	"github.com/chenyme/grok2api/backend/internal/infra/provider"
	"github.com/chenyme/grok2api/backend/internal/infra/provider/conversation"
	"github.com/chenyme/grok2api/backend/internal/infra/security"
)

const (
	// QuotaMode 是 xAI Official 本地额度窗口的模式名。
	QuotaMode = "xai_official"
	// DefaultQuotaLimit/Window 只提供并发与冷却载体;真实限额由官方 429 驱动。
	DefaultQuotaLimit  = 1000
	DefaultQuotaWindow = 3600
	// maxResponseBytes 限制非流式对话响应体积,与 Console 保持一致。
	maxResponseBytes = 64 << 20
)

type Config struct {
	// BaseURL 固定为官方地址;为防止凭据被发送到恶意地址,不提供后台修改入口。
	BaseURL        string
	TimeoutSeconds int
}

type Adapter struct {
	mu     sync.RWMutex
	cfg    Config
	egress *infraegress.Manager
	cipher *security.Cipher
}

func NewAdapter(cfg Config, egress *infraegress.Manager, cipher *security.Cipher) *Adapter {
	if strings.TrimSpace(cfg.BaseURL) == "" {
		cfg.BaseURL = "https://api.x.ai/v1"
	}
	if cfg.TimeoutSeconds <= 0 {
		cfg.TimeoutSeconds = 600
	}
	return &Adapter{cfg: cfg, egress: egress, cipher: cipher}
}

func (a *Adapter) Provider() account.Provider { return account.ProviderXAIOfficial }

func (a *Adapter) UpdateConfig(cfg Config) {
	a.mu.Lock()
	if strings.TrimSpace(cfg.BaseURL) == "" {
		cfg.BaseURL = a.cfg.BaseURL
	}
	if cfg.TimeoutSeconds <= 0 {
		cfg.TimeoutSeconds = a.cfg.TimeoutSeconds
	}
	a.cfg = cfg
	a.mu.Unlock()
}

func (a *Adapter) config() Config {
	a.mu.RLock()
	defer a.mu.RUnlock()
	return a.cfg
}

func (a *Adapter) QuotaMode(string) string { return QuotaMode }

func (a *Adapter) TierOrder(string) []account.WebTier { return nil }

func (a *Adapter) PricingModel(upstreamModel string) string { return upstreamModel }

func (a *Adapter) ParseImportedCredentials(data []byte) ([]provider.CredentialSeed, error) {
	return parseImportedCredentials(data)
}

func (a *Adapter) MarshalCredentials(values []provider.CredentialSeed) ([]byte, error) {
	return marshalCredentials(values)
}

// SyncQuota 返回本地额度窗口;官方 API Key 没有可查询的远端窗口。
func (a *Adapter) SyncQuota(_ context.Context, credential account.Credential) (provider.QuotaSnapshot, error) {
	now := time.Now().UTC()
	resetAt := now.Add(DefaultQuotaWindow * time.Second)
	return provider.QuotaSnapshot{SyncedAt: now, Windows: []account.QuotaWindow{{
		AccountID: credential.ID, Mode: QuotaMode, Remaining: DefaultQuotaLimit, Total: DefaultQuotaLimit,
		WindowSeconds: DefaultQuotaWindow, ResetAt: &resetAt, SyncedAt: &now, Source: account.QuotaSourceDefault, UpdatedAt: now,
	}}}, nil
}

func (a *Adapter) SyncQuotaMode(ctx context.Context, credential account.Credential, mode string) (account.QuotaWindow, error) {
	if mode != QuotaMode {
		return account.QuotaWindow{}, fmt.Errorf("不支持的 xAI Official 额度模式 %q", mode)
	}
	snapshot, err := a.SyncQuota(ctx, credential)
	if err != nil {
		return account.QuotaWindow{}, err
	}
	return snapshot.Windows[0], nil
}

// ListModels 调用官方模型目录;同一响应也是 Key 健康验证的依据。
func (a *Adapter) ListModels(ctx context.Context, credential account.Credential) ([]string, error) {
	key, err := a.cipher.Decrypt(credential.EncryptedAccessToken)
	if err != nil {
		return nil, err
	}
	cfg := a.config()
	requestCtx, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	lease, err := a.egress.AcquireCredential(requestCtx, egressdomain.ScopeXAIOfficial, credential)
	if err != nil {
		return nil, err
	}
	defer lease.Release()
	request, err := http.NewRequestWithContext(requestCtx, http.MethodGet, endpoint(cfg.BaseURL, "/models"), nil)
	if err != nil {
		return nil, err
	}
	request.Header.Set("Authorization", "Bearer "+key)
	response, err := lease.Do(request)
	if err != nil {
		a.egress.FeedbackForScope(context.WithoutCancel(ctx), egressdomain.ScopeXAIOfficial, lease.NodeID, 0, err)
		return nil, err
	}
	defer func() { _ = response.Body.Close() }()
	a.egress.FeedbackForScope(context.WithoutCancel(ctx), egressdomain.ScopeXAIOfficial, lease.NodeID, response.StatusCode, nil)
	data, _, err := provider.ReadDiagnosticBody(response.Body)
	if err != nil {
		return nil, err
	}
	if response.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("xAI 模型目录返回 %d: %s", response.StatusCode, truncateForError(data))
	}
	var payload struct {
		Data []struct {
			ID string `json:"id"`
		} `json:"data"`
	}
	if err := json.Unmarshal(data, &payload); err != nil {
		return nil, fmt.Errorf("解析 xAI 模型目录: %w", err)
	}
	values := make([]string, 0, len(payload.Data))
	for _, item := range payload.Data {
		if strings.TrimSpace(item.ID) != "" {
			values = append(values, item.ID)
		}
	}
	return values, nil
}

var storedResponsePath = regexp.MustCompile(`^/responses/[A-Za-z0-9_.:-]{1,256}$`)

// ForwardResponse 将统一对话请求准透传到官方 api.x.ai。
// 官方本身实现标准 Responses 协议,因此除 chat/messages 的协议转换外
// 不做请求改写;403 是权限语义而非出口拦截,直接透传诊断。
func (a *Adapter) ForwardResponse(ctx context.Context, request provider.ResponseResourceRequest) (*provider.Response, error) {
	switch {
	case request.Method == http.MethodPost && request.Path == "/responses":
	case (request.Method == http.MethodGet || request.Method == http.MethodDelete) && storedResponsePath.MatchString(request.Path):
	default:
		return jsonProviderResponse(http.StatusBadRequest, map[string]any{"error": map[string]any{"type": "invalid_request_error", "message": "xAI Official 不支持该接口"}}), nil
	}
	key, err := a.cipher.Decrypt(request.Credential.EncryptedAccessToken)
	if err != nil {
		return nil, err
	}
	body := request.Body
	var conversationOptions conversation.ResponseOptions
	if request.NormalizeBody && request.Method == http.MethodPost {
		if request.Operation == conversation.OperationMessages {
			body, conversationOptions, err = conversation.ConvertRequestWithOptions(body, request.Model, request.Operation)
		} else {
			body, err = conversation.ConvertRequest(body, request.Model, request.Operation)
		}
		if err != nil {
			return invalidConversationResponse(request.Operation, err), nil
		}
	}
	cfg := a.config()
	requestCtx, cancel := context.WithTimeout(ctx, time.Duration(cfg.TimeoutSeconds)*time.Second)
	lease, err := a.egress.AcquireCredential(requestCtx, egressdomain.ScopeXAIOfficial, request.Credential)
	if err != nil {
		cancel()
		return nil, err
	}
	var payload io.Reader
	if request.Method == http.MethodPost {
		payload = bytes.NewReader(body)
	}
	upstream, err := http.NewRequestWithContext(requestCtx, request.Method, endpoint(cfg.BaseURL, request.Path), payload)
	if err != nil {
		lease.Release()
		cancel()
		return nil, err
	}
	upstream.Header.Set("Authorization", "Bearer "+key)
	if request.Method == http.MethodPost {
		upstream.Header.Set("Content-Type", "application/json")
	}
	if request.Streaming {
		upstream.Header.Set("Accept", "text/event-stream")
	}
	response, err := lease.Do(upstream)
	if err != nil {
		a.egress.FeedbackForScope(context.WithoutCancel(ctx), egressdomain.ScopeXAIOfficial, lease.NodeID, 0, err)
		lease.Release()
		cancel()
		return nil, err
	}
	responseBodyTruncated := false
	var rateLimit *provider.RateLimitMetadata
	if response.StatusCode == http.StatusTooManyRequests {
		data, truncated, readErr := provider.ReadDiagnosticBody(response.Body)
		_ = response.Body.Close()
		if readErr != nil {
			lease.Release()
			cancel()
			return nil, readErr
		}
		responseBodyTruncated = truncated
		rateLimit = provider.RateLimitFromResponse(response.StatusCode, response.Header, data)
		response.Body = io.NopCloser(bytes.NewReader(data))
		response.ContentLength = int64(len(data))
		response.Header.Set("Content-Length", strconv.Itoa(len(data)))
	}
	release := func() {
		a.egress.FeedbackForScope(context.WithoutCancel(ctx), egressdomain.ScopeXAIOfficial, lease.NodeID, response.StatusCode, nil)
		lease.Release()
		cancel()
	}
	if request.Operation == conversation.OperationChat || request.Operation == conversation.OperationMessages {
		if request.Streaming && response.StatusCode >= 200 && response.StatusCode < 300 {
			response.Body = conversation.ConvertResponseStreamWithOptions(response.Body, request.Operation, conversationOptions)
			response.Header.Del("Content-Length")
			response.Header.Set("Content-Type", "text/event-stream")
			result := responseResult(response, &releaseBody{ReadCloser: response.Body, release: release})
			result.RateLimit = rateLimit
			return result, nil
		}
		var data []byte
		var readErr error
		var diagnosticTruncated bool
		if response.StatusCode >= 200 && response.StatusCode < 300 {
			data, readErr = io.ReadAll(io.LimitReader(response.Body, maxResponseBytes+1))
		} else {
			data, diagnosticTruncated, readErr = provider.ReadDiagnosticBody(response.Body)
			diagnosticTruncated = diagnosticTruncated || responseBodyTruncated
		}
		_ = response.Body.Close()
		release()
		if readErr != nil {
			return nil, readErr
		}
		if response.StatusCode >= 200 && response.StatusCode < 300 && len(data) > maxResponseBytes {
			return nil, fmt.Errorf("xAI 对话响应超过 64 MiB")
		}
		if response.StatusCode < 200 || response.StatusCode >= 300 {
			diagnostic := &provider.DiagnosticResponse{StatusCode: response.StatusCode, Status: response.Status, Header: response.Header.Clone(), Body: data, BodyTruncated: diagnosticTruncated}
			converted := normalizeConversationError(data, request.Operation, response.StatusCode)
			response.Header.Set("Content-Length", strconv.Itoa(len(converted)))
			response.Header.Set("Content-Type", "application/json")
			result := responseResult(response, io.NopCloser(bytes.NewReader(converted)))
			result.Diagnostic = diagnostic
			result.RateLimit = rateLimit
			return result, nil
		}
		converted, convertErr := conversation.ConvertResponseJSONWithOptions(data, request.Operation, conversationOptions)
		if convertErr != nil {
			return nil, convertErr
		}
		response.Header.Set("Content-Length", strconv.Itoa(len(converted)))
		response.Header.Set("Content-Type", "application/json")
		result := responseResult(response, io.NopCloser(bytes.NewReader(converted)))
		result.RateLimit = rateLimit
		return result, nil
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		data, truncated, readErr := provider.ReadDiagnosticBody(response.Body)
		_ = response.Body.Close()
		release()
		if readErr != nil {
			return nil, readErr
		}
		diagnostic := &provider.DiagnosticResponse{StatusCode: response.StatusCode, Status: response.Status, Header: response.Header.Clone(), Body: data, BodyTruncated: truncated || responseBodyTruncated}
		response.Header.Set("Content-Length", strconv.Itoa(len(data)))
		result := responseResult(response, io.NopCloser(bytes.NewReader(data)))
		result.Diagnostic = diagnostic
		result.RateLimit = rateLimit
		return result, nil
	}
	result := responseResult(response, &releaseBody{ReadCloser: response.Body, release: release})
	result.RateLimit = rateLimit
	return result, nil
}

func normalizeConversationError(data []byte, operation string, status int) []byte {
	var envelope struct {
		Error   json.RawMessage `json:"error"`
		Message string          `json:"message"`
	}
	if json.Unmarshal(data, &envelope) == nil && len(bytes.TrimSpace(envelope.Error)) > 0 && string(bytes.TrimSpace(envelope.Error)) != "null" {
		if converted, err := conversation.ConvertResponseJSON(data, operation); err == nil {
			return converted
		}
	}
	message := strings.TrimSpace(envelope.Message)
	if message == "" {
		message = strings.TrimSpace(string(data))
	}
	if message == "" {
		message = http.StatusText(status)
	}
	if len(message) > 4096 {
		message = message[:4096]
	}
	errorType := conversationErrorType(status, operation)
	if operation == conversation.OperationMessages {
		result, _ := json.Marshal(map[string]any{"type": "error", "error": map[string]any{"type": errorType, "message": message}})
		return result
	}
	result, _ := json.Marshal(map[string]any{"error": map[string]any{"type": errorType, "message": message}})
	return result
}

func conversationErrorType(status int, operation string) string {
	switch status {
	case http.StatusBadRequest, http.StatusUnprocessableEntity:
		return "invalid_request_error"
	case http.StatusUnauthorized:
		return "authentication_error"
	case http.StatusForbidden:
		return "permission_error"
	case http.StatusNotFound:
		return "not_found_error"
	case http.StatusTooManyRequests:
		return "rate_limit_error"
	case http.StatusServiceUnavailable:
		if operation == conversation.OperationMessages {
			return "overloaded_error"
		}
	}
	if operation == conversation.OperationMessages {
		return "api_error"
	}
	return "server_error"
}

func endpoint(baseURL, path string) string {
	baseURL = strings.TrimRight(strings.TrimSpace(baseURL), "/")
	if !strings.HasSuffix(baseURL, "/v1") {
		baseURL += "/v1"
	}
	return baseURL + path
}

func truncateForError(data []byte) string {
	text := strings.TrimSpace(string(data))
	if len(text) > 256 {
		text = text[:256]
	}
	return text
}

func responseResult(response *http.Response, body io.ReadCloser) *provider.Response {
	upstreamURL := ""
	if response.Request != nil && response.Request.URL != nil {
		upstreamURL = response.Request.URL.String()
	}
	return &provider.Response{
		StatusCode: response.StatusCode, Status: response.Status, Header: response.Header.Clone(), Body: body, QuotaUnits: 1, UpstreamURL: upstreamURL,
	}
}

func jsonProviderResponse(status int, value any) *provider.Response {
	data, _ := json.Marshal(value)
	header := http.Header{}
	header.Set("Content-Type", "application/json")
	header.Set("Content-Length", strconv.Itoa(len(data)))
	return &provider.Response{StatusCode: status, Status: http.StatusText(status), Header: header, Body: io.NopCloser(bytes.NewReader(data))}
}

func invalidConversationResponse(operation string, err error) *provider.Response {
	message := "请求无效"
	if err != nil {
		message = err.Error()
	}
	if operation == conversation.OperationMessages {
		return jsonProviderResponse(http.StatusBadRequest, map[string]any{"type": "error", "error": map[string]any{"type": "invalid_request_error", "message": message}})
	}
	return jsonProviderResponse(http.StatusBadRequest, map[string]any{"error": map[string]any{"type": "invalid_request_error", "message": message}})
}

type releaseBody struct {
	io.ReadCloser
	once    sync.Once
	release func()
}

func (b *releaseBody) Close() error {
	err := b.ReadCloser.Close()
	b.once.Do(b.release)
	return err
}
