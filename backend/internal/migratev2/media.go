package migratev2

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"

	mediadomain "github.com/chenyme/grok2api/backend/internal/domain/media"
	inframedia "github.com/chenyme/grok2api/backend/internal/infra/media"
	"github.com/chenyme/grok2api/backend/internal/infra/persistence/relational"
	"github.com/chenyme/grok2api/backend/internal/repository"
)

// mediaImporter 按清单从仍在运行的 v2 服务拉取媒体文件,校验后写入
// v3 本地媒体存储与 media_assets。重复执行按资产 ID 幂等跳过。
type mediaImporter struct {
	assets  *relational.MediaAssetRepository
	store   *inframedia.LocalStore
	client  *http.Client
	baseURL string
}

func newMediaImporter(database *relational.Database, options ImportOptions) (*mediaImporter, error) {
	if strings.TrimSpace(options.MediaRoot) == "" {
		return nil, fmt.Errorf("媒体导入需要 --media-root(v3 media.local.path)")
	}
	base := strings.TrimRight(strings.TrimSpace(options.V2BaseURL), "/")
	if _, err := url.Parse(base); err != nil || !strings.HasPrefix(base, "http") {
		return nil, fmt.Errorf("无效的 --v2-base-url: %q", options.V2BaseURL)
	}
	store, err := inframedia.NewLocalStore(options.MediaRoot)
	if err != nil {
		return nil, fmt.Errorf("打开 v3 媒体目录: %w", err)
	}
	return &mediaImporter{
		assets:  relational.NewMediaAssetRepository(database),
		store:   store,
		client:  &http.Client{Timeout: 5 * time.Minute},
		baseURL: base,
	}, nil
}

func (m *mediaImporter) run(ctx context.Context, entries []MediaEntry, dryRun bool, report *Report) error {
	report.Media.Found = len(entries)
	for _, entry := range entries {
		if err := ctx.Err(); err != nil {
			return err
		}
		assetID := LegacyAssetID(entry.FileName)
		mimeType, ok := legacyMIME(entry.Kind, fileExt(entry.FileName))
		if !ok {
			report.Media.Skipped++
			report.Problems = append(report.Problems, fmt.Sprintf("跳过不支持的媒体类型: %s", entry.FileName))
			continue
		}
		if _, err := m.assets.GetMediaAsset(ctx, assetID); err == nil {
			report.Media.Skipped++ // 已导入,幂等跳过
			continue
		} else if err != repository.ErrNotFound {
			return fmt.Errorf("查询媒体资产 %s: %w", assetID, err)
		}
		if dryRun {
			report.Media.Skipped++
			continue
		}
		if err := m.importOne(ctx, entry, assetID, mimeType); err != nil {
			report.Media.Failed++
			report.Problems = append(report.Problems, fmt.Sprintf("媒体 %s: %v", entry.FileName, err))
			continue
		}
		report.Media.Imported++
	}
	return nil
}

func (m *mediaImporter) importOne(ctx context.Context, entry MediaEntry, assetID, mimeType string) error {
	data, err := m.fetch(ctx, entry)
	if err != nil {
		return err
	}
	if len(data) == 0 {
		return fmt.Errorf("响应为空")
	}
	if int64(len(data)) > relational.MaxVideoAssetBytes {
		return fmt.Errorf("超过 256 MiB 上限")
	}
	sum := sha256.Sum256(data)
	var storageKey string
	if entry.Kind == "video" {
		storageKey, err = m.store.SaveVideo(ctx, assetID, mimeType, data)
	} else {
		storageKey, err = m.store.SaveImage(ctx, assetID, mimeType, data)
	}
	if err != nil {
		return fmt.Errorf("写入媒体存储: %w", err)
	}
	createdAt := time.Now()
	if entry.CreatedNS > 0 {
		createdAt = time.Unix(0, entry.CreatedNS)
	}
	err = m.assets.CreateMediaAsset(ctx, mediadomain.Asset{
		ID:         assetID,
		Kind:       entry.Kind,
		StorageKey: storageKey,
		MIMEType:   mimeType,
		SizeBytes:  int64(len(data)),
		SHA256:     hex.EncodeToString(sum[:]),
		Origin:     mediadomain.OriginLegacyImport,
		CreatedAt:  createdAt,
	})
	if err != nil {
		return fmt.Errorf("写入媒体元数据: %w", err)
	}
	return nil
}

// fetch 通过 v2 的历史文件接口按 ID 拉取文件内容。
func (m *mediaImporter) fetch(ctx context.Context, entry MediaEntry) ([]byte, error) {
	endpoint := "/v1/files/image"
	if entry.Kind == "video" {
		endpoint = "/v1/files/video"
	}
	target := m.baseURL + endpoint + "?id=" + url.QueryEscape(entry.AssetID())
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, target, nil)
	if err != nil {
		return nil, err
	}
	response, err := m.client.Do(request)
	if err != nil {
		return nil, fmt.Errorf("拉取 v2 媒体: %w", err)
	}
	defer func() { _ = response.Body.Close() }()
	if response.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("v2 返回 %d", response.StatusCode)
	}
	return io.ReadAll(io.LimitReader(response.Body, relational.MaxVideoAssetBytes+1))
}

var legacyAssetIDPattern = regexp.MustCompile(`^[0-9a-fA-F-]{16,36}$`)

// LegacyAssetID 把 v2 文件名映射为满足 v3 约束(16-64 字符)的稳定资产 ID。
// 合规的 v2 ID 原样保留,保证旧媒体 URL 兼容入口可以直接换算;
// 不合规的退化为文件名哈希派生的 legacy- 前缀 ID。
func LegacyAssetID(fileName string) string {
	id := fileName
	if i := strings.LastIndexByte(fileName, '.'); i > 0 {
		id = fileName[:i]
	}
	if legacyAssetIDPattern.MatchString(id) {
		return strings.ToLower(id)
	}
	sum := sha256.Sum256([]byte(id))
	return "legacy-" + hex.EncodeToString(sum[:])[:32]
}

// legacyMIME 按 v2 扩展名推导 v3 允许的 MIME;不在 v3 白名单内的返回 false。
func legacyMIME(kind, ext string) (string, bool) {
	switch kind {
	case "image":
		switch strings.ToLower(ext) {
		case ".jpg", ".jpeg":
			return "image/jpeg", true
		case ".png":
			return "image/png", true
		case ".webp":
			return "image/webp", true
		case ".gif":
			return "image/gif", true
		}
	case "video":
		switch strings.ToLower(ext) {
		case ".mp4", ".m4v":
			return "video/mp4", true
		case ".webm":
			return "video/webm", true
		case ".mov":
			return "video/quicktime", true
		}
	}
	return "", false
}
