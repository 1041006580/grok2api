// grok2api-migrate-v2 是 v2(Python/Redis)到 v3 的一次性离线迁移工具。
//
// 用法:
//
//	grok2api-migrate-v2 inspect --redis-url <dsn> [--image-list f] [--video-list f] [--media-db f]
//	grok2api-migrate-v2 export  --config config.yaml --redis-url <dsn> --archive out.enc [--report r.json] [清单参数]
//	grok2api-migrate-v2 import  --config config.yaml --archive out.enc [--v2-base-url https://...] [--dry-run] [--report r.json]
//	grok2api-migrate-v2 verify  --config config.yaml --archive out.enc [--report r.json]
//
// 档案用 config.yaml 的 secrets.credentialEncryptionKey 加密;inspect 只
// 统计不落盘。工具全程只读 v2,可重复执行。
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"

	"github.com/chenyme/grok2api/backend/internal/infra/config"
	"github.com/chenyme/grok2api/backend/internal/infra/persistence/relational"
	"github.com/chenyme/grok2api/backend/internal/infra/security"
	"github.com/chenyme/grok2api/backend/internal/migratev2"
)

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, "错误:", err)
		os.Exit(1)
	}
}

type options struct {
	configPath string
	redisURL   string
	archive    string
	report     string
	imageList  string
	videoList  string
	mediaDB    string
	v2BaseURL  string
	dryRun     bool
}

func run(args []string) error {
	if len(args) == 0 {
		return fmt.Errorf("缺少子命令: inspect | export | import | verify")
	}
	command := args[0]
	flags := flag.NewFlagSet("grok2api-migrate-v2 "+command, flag.ContinueOnError)
	var opts options
	flags.StringVar(&opts.configPath, "config", "", "v3 config.yaml 路径")
	flags.StringVar(&opts.redisURL, "redis-url", "", "v2 Redis DSN(只读)")
	flags.StringVar(&opts.archive, "archive", "", "加密迁移档案路径")
	flags.StringVar(&opts.report, "report", "", "非敏感报告输出路径(JSON)")
	flags.StringVar(&opts.imageList, "image-list", "", "v2 图片文件名清单(ls data/files/images)")
	flags.StringVar(&opts.videoList, "video-list", "", "v2 视频文件名清单(ls data/files/videos)")
	flags.StringVar(&opts.mediaDB, "media-db", "", "v2 local_media_cache.db 路径(可选)")
	flags.StringVar(&opts.v2BaseURL, "v2-base-url", "", "v2 服务地址,导入媒体时使用")
	flags.BoolVar(&opts.dryRun, "dry-run", false, "只统计不写入")
	if err := flags.Parse(args[1:]); err != nil {
		return err
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	switch command {
	case "inspect":
		return runInspect(ctx, opts)
	case "export":
		return runExport(ctx, opts)
	case "import":
		return runImport(ctx, opts)
	case "verify":
		return runVerify(ctx, opts)
	default:
		return fmt.Errorf("未知子命令 %q: inspect | export | import | verify", command)
	}
}

func runInspect(ctx context.Context, opts options) error {
	if opts.redisURL == "" {
		return fmt.Errorf("inspect 需要 --redis-url")
	}
	_, report, err := migratev2.Export(ctx, exportOptions(opts))
	if err != nil {
		return err
	}
	report.Stage = "inspect"
	return emitReport(report, opts.report)
}

func runExport(ctx context.Context, opts options) error {
	if opts.redisURL == "" || opts.archive == "" {
		return fmt.Errorf("export 需要 --redis-url 和 --archive")
	}
	cipher, err := loadCipher(opts)
	if err != nil {
		return err
	}
	archive, report, err := migratev2.Export(ctx, exportOptions(opts))
	if err != nil {
		return err
	}
	if err := migratev2.WriteArchive(opts.archive, cipher, archive); err != nil {
		return err
	}
	return emitReport(report, opts.report)
}

func runImport(ctx context.Context, opts options) error {
	if opts.archive == "" {
		return fmt.Errorf("import 需要 --archive")
	}
	cfg, cipher, err := loadConfigAndCipher(opts)
	if err != nil {
		return err
	}
	archive, err := migratev2.ReadArchive(opts.archive, cipher)
	if err != nil {
		return err
	}
	database, err := openDatabase(ctx, cfg)
	if err != nil {
		return err
	}
	defer database.Close()
	importOptions := migratev2.ImportOptions{
		DryRun:    opts.dryRun,
		V2BaseURL: opts.v2BaseURL,
		MediaRoot: cfg.Media.Local.Path,
	}
	importer, err := migratev2.NewImporter(database, cipher, importOptions)
	if err != nil {
		return err
	}
	report, err := importer.Run(ctx, archive, importOptions)
	if reportErr := emitReport(report, opts.report); reportErr != nil && err == nil {
		err = reportErr
	}
	return err
}

func runVerify(ctx context.Context, opts options) error {
	if opts.archive == "" {
		return fmt.Errorf("verify 需要 --archive")
	}
	cfg, cipher, err := loadConfigAndCipher(opts)
	if err != nil {
		return err
	}
	archive, err := migratev2.ReadArchive(opts.archive, cipher)
	if err != nil {
		return err
	}
	database, err := openDatabase(ctx, cfg)
	if err != nil {
		return err
	}
	defer database.Close()
	report, err := migratev2.Verify(ctx, database, archive, cfg.Media.Local.Path)
	if reportErr := emitReport(report, opts.report); reportErr != nil && err == nil {
		err = reportErr
	}
	if err == nil && len(report.Problems) > 0 {
		return fmt.Errorf("verify 发现 %d 个问题,详见报告", len(report.Problems))
	}
	return err
}

func exportOptions(opts options) migratev2.ExportOptions {
	return migratev2.ExportOptions{
		RedisURL:  opts.redisURL,
		ImageList: opts.imageList,
		VideoList: opts.videoList,
		MediaDB:   opts.mediaDB,
	}
}

func loadConfigAndCipher(opts options) (config.Config, *security.Cipher, error) {
	if opts.configPath == "" {
		return config.Config{}, nil, fmt.Errorf("需要 --config(v3 config.yaml)")
	}
	cfg, err := config.Load(opts.configPath)
	if err != nil {
		return config.Config{}, nil, err
	}
	cipher, err := security.NewCipher(cfg.Secrets.CredentialEncryptionKey)
	if err != nil {
		return config.Config{}, nil, err
	}
	return cfg, cipher, nil
}

func loadCipher(opts options) (*security.Cipher, error) {
	_, cipher, err := loadConfigAndCipher(opts)
	return cipher, err
}

func openDatabase(ctx context.Context, cfg config.Config) (*relational.Database, error) {
	var database *relational.Database
	var err error
	switch cfg.Database.Driver {
	case "sqlite":
		database, err = relational.OpenSQLite(ctx, cfg.Database.SQLite.Path)
	case "postgres":
		database, err = relational.OpenPostgres(ctx, cfg.Database.Postgres.DSN, cfg.Database.Postgres.MaxOpenConns, cfg.Database.Postgres.MaxIdleConns)
	default:
		return nil, fmt.Errorf("不支持的数据库驱动: %s", cfg.Database.Driver)
	}
	if err != nil {
		return nil, err
	}
	if err := database.InitializeSchema(ctx); err != nil {
		database.Close()
		return nil, err
	}
	return database, nil
}

// emitReport 将报告写入文件(可选)并打印摘要;报告不含任何凭据。
func emitReport(report migratev2.Report, path string) error {
	if path != "" {
		if err := migratev2.WriteReport(path, report); err != nil {
			return fmt.Errorf("写入报告: %w", err)
		}
	}
	summary, err := json.MarshalIndent(struct {
		Stage    string                `json:"stage"`
		Accounts migratev2.ReportCounts `json:"accounts"`
		XAIKeys  migratev2.ReportCounts `json:"xai_keys"`
		Media    migratev2.ReportCounts `json:"media"`
		Problems int                   `json:"problems"`
	}{report.Stage, report.Accounts, report.XAIKeys, report.Media, len(report.Problems)}, "", "  ")
	if err != nil {
		return err
	}
	fmt.Println(string(summary))
	for _, problem := range report.Problems {
		fmt.Fprintln(os.Stderr, "问题:", problem)
	}
	return nil
}
