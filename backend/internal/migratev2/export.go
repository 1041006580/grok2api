package migratev2

import (
	"bufio"
	"context"
	"fmt"
	"os"
	"sort"
	"strings"

	"github.com/glebarez/sqlite"
	"gorm.io/gorm"
	gormlogger "gorm.io/gorm/logger"
)

// ExportOptions 控制 export/inspect 的数据源。
type ExportOptions struct {
	RedisURL  string
	ImageList string // v2 `ls data/files/images` 输出的清单文件,可为空
	VideoList string // v2 `ls data/files/videos` 输出的清单文件,可为空
	MediaDB   string // v2 local_media_cache.db,可为空(默认配置下该库常为空)
}

// Export 读取 v2 全量数据并组装档案与报告。dry-run 时调用方不落盘档案。
func Export(ctx context.Context, options ExportOptions) (Archive, Report, error) {
	report := Report{Stage: "export", StartedAtMS: nowMS()}
	source, closeSource, err := OpenSource(ctx, options.RedisURL)
	if err != nil {
		return Archive{}, report, err
	}
	defer func() { _ = closeSource() }()

	accounts, skipped, err := source.LoadAccounts(ctx)
	if err != nil {
		return Archive{}, report, err
	}
	report.Accounts = ReportCounts{Found: len(accounts), Skipped: skipped}

	config, xaiKeys, err := source.LoadConfig(ctx)
	if err != nil {
		return Archive{}, report, err
	}
	report.XAIKeys = ReportCounts{Found: len(xaiKeys)}
	report.Config = BuildConfigReport(config)

	media, problems, err := loadMediaManifest(options)
	if err != nil {
		return Archive{}, report, err
	}
	report.Media = ReportCounts{Found: len(media)}
	report.Problems = append(report.Problems, problems...)
	report.FinishedAtMS = nowMS()

	archive := Archive{
		FormatVersion: ArchiveFormatVersion,
		ExportedAtMS:  nowMS(),
		Accounts:      accounts,
		XAIKeys:       xaiKeys,
		Config:        config,
		Media:         media,
	}
	return archive, report, nil
}

// loadMediaManifest 合并文件名清单与可选的 SQLite 索引。
// 清单是权威来源(v2 默认配置下 SQLite 索引不写入);索引仅补充大小与时间。
func loadMediaManifest(options ExportOptions) ([]MediaEntry, []string, error) {
	var problems []string
	entries := make([]MediaEntry, 0, 64)
	seen := make(map[string]struct{})

	appendList := func(path, kind string, allowed map[string]struct{}) error {
		if path == "" {
			return nil
		}
		names, err := readNameList(path)
		if err != nil {
			return err
		}
		for _, name := range names {
			ext := strings.ToLower(fileExt(name))
			if _, ok := allowed[ext]; !ok {
				problems = append(problems, fmt.Sprintf("跳过 %s 清单中的非媒体文件: %s", kind, name))
				continue
			}
			key := kind + "\x00" + name
			if _, ok := seen[key]; ok {
				continue
			}
			seen[key] = struct{}{}
			entries = append(entries, MediaEntry{Kind: kind, FileName: name})
		}
		return nil
	}
	if err := appendList(options.ImageList, "image", v2ImageExtensions); err != nil {
		return nil, nil, err
	}
	if err := appendList(options.VideoList, "video", v2VideoExtensions); err != nil {
		return nil, nil, err
	}

	if options.MediaDB != "" {
		index, err := readMediaIndex(options.MediaDB)
		if err != nil {
			return nil, nil, err
		}
		for i := range entries {
			if meta, ok := index[entries[i].Kind+"\x00"+entries[i].FileName]; ok {
				entries[i].SizeBytes = meta.SizeBytes
				entries[i].CreatedNS = meta.CreatedNS
			}
		}
		// 索引中存在但清单缺失的文件说明清单不完整,必须显式暴露。
		for key, meta := range index {
			if _, ok := seen[key]; !ok {
				problems = append(problems, fmt.Sprintf("SQLite 索引中的 %s 未出现在清单: %s", strings.SplitN(key, "\x00", 2)[0], meta.FileName))
			}
		}
	}
	sort.Slice(entries, func(i, j int) bool {
		if entries[i].Kind != entries[j].Kind {
			return entries[i].Kind < entries[j].Kind
		}
		return entries[i].FileName < entries[j].FileName
	})
	return entries, problems, nil
}

// v2 媒体索引接受的扩展名(与 v2 media_cache.py 一致)。
var (
	v2ImageExtensions = map[string]struct{}{".jpg": {}, ".jpeg": {}, ".png": {}, ".gif": {}, ".webp": {}, ".bmp": {}}
	v2VideoExtensions = map[string]struct{}{".mp4": {}, ".mov": {}, ".m4v": {}, ".webm": {}, ".avi": {}, ".mkv": {}}
)

func fileExt(name string) string {
	for i := len(name) - 1; i >= 0; i-- {
		if name[i] == '.' {
			return name[i:]
		}
	}
	return ""
}

func readNameList(path string) ([]string, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("读取媒体清单: %w", err)
	}
	defer func() { _ = file.Close() }()
	var names []string
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		name := strings.TrimSpace(scanner.Text())
		if name == "" || strings.HasPrefix(name, "#") {
			continue
		}
		names = append(names, name)
	}
	return names, scanner.Err()
}

type mediaIndexEntry struct {
	FileName  string
	SizeBytes int64
	CreatedNS int64
}

// readMediaIndex 只读打开 v2 local_media_cache.db 并读取 local_media_files 表。
func readMediaIndex(path string) (map[string]mediaIndexEntry, error) {
	if _, err := os.Stat(path); err != nil {
		return nil, fmt.Errorf("打开 v2 媒体索引: %w", err)
	}
	db, err := gorm.Open(sqlite.Open(path+"?mode=ro"), &gorm.Config{Logger: gormlogger.Discard})
	if err != nil {
		return nil, fmt.Errorf("打开 v2 媒体索引: %w", err)
	}
	sqlDB, err := db.DB()
	if err != nil {
		return nil, err
	}
	defer func() { _ = sqlDB.Close() }()

	type row struct {
		MediaType   string
		Name        string
		SizeBytes   int64
		CreatedAtNS int64
	}
	var rows []row
	if err := db.Table("local_media_files").
		Select("media_type, name, size_bytes, created_at_ns").
		Scan(&rows).Error; err != nil {
		return nil, fmt.Errorf("读取 v2 媒体索引: %w", err)
	}
	index := make(map[string]mediaIndexEntry, len(rows))
	for _, r := range rows {
		index[r.MediaType+"\x00"+r.Name] = mediaIndexEntry{FileName: r.Name, SizeBytes: r.SizeBytes, CreatedNS: r.CreatedAtNS}
	}
	return index, nil
}
