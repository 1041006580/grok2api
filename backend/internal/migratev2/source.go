package migratev2

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"
	"strconv"
	"strings"

	"github.com/redis/go-redis/v9"
)

// v2 Redis key 布局(与 v2 的 app/control/account/backends/redis.py 一致,无前缀)。
const (
	v2KeyAccountRecord = "accounts:record:"
	v2KeyPoolPrefix    = "accounts:pool:"
	v2KeyConfigUser    = "config:user"
	v2ConfigXAIKeys    = "xai.keys"
)

var v2Pools = []string{"basic", "super", "heavy"}

// redisReader 是迁移工具对 v2 Redis 的最小只读视图,便于测试替换。
type redisReader interface {
	SMembers(ctx context.Context, key string) ([]string, error)
	HGetAll(ctx context.Context, key string) (map[string]string, error)
}

type goRedisReader struct{ client *redis.Client }

func (r goRedisReader) SMembers(ctx context.Context, key string) ([]string, error) {
	return r.client.SMembers(ctx, key).Result()
}

func (r goRedisReader) HGetAll(ctx context.Context, key string) (map[string]string, error) {
	return r.client.HGetAll(ctx, key).Result()
}

// Source 以只读方式读取 v2 Redis 中的账号与配置。
type Source struct {
	reader redisReader
}

// OpenSource 按 DSN 连接 v2 Redis 并验证连通性。调用方负责保证只读使用。
func OpenSource(ctx context.Context, redisURL string) (*Source, func() error, error) {
	options, err := redis.ParseURL(redisURL)
	if err != nil {
		return nil, nil, fmt.Errorf("解析 v2 Redis DSN: %w", err)
	}
	client := redis.NewClient(options)
	if err := client.Ping(ctx).Err(); err != nil {
		_ = client.Close()
		return nil, nil, fmt.Errorf("连接 v2 Redis: %w", err)
	}
	return &Source{reader: goRedisReader{client: client}}, client.Close, nil
}

// LoadAccounts 读取全部未删除账号。软删除(deleted_at 非空)记录被过滤,
// 数量计入返回的 skipped。
func (s *Source) LoadAccounts(ctx context.Context) (accounts []V2Account, skipped int, err error) {
	tokens := make([]string, 0, 64)
	seen := make(map[string]struct{})
	for _, pool := range v2Pools {
		members, err := s.reader.SMembers(ctx, v2KeyPoolPrefix+pool)
		if err != nil {
			return nil, 0, fmt.Errorf("读取 v2 账号池 %s: %w", pool, err)
		}
		for _, token := range members {
			if _, ok := seen[token]; !ok {
				seen[token] = struct{}{}
				tokens = append(tokens, token)
			}
		}
	}
	sort.Strings(tokens)
	accounts = make([]V2Account, 0, len(tokens))
	for _, token := range tokens {
		record, err := s.reader.HGetAll(ctx, v2KeyAccountRecord+token)
		if err != nil {
			return nil, 0, fmt.Errorf("读取 v2 账号记录: %w", err)
		}
		if len(record) == 0 {
			// 池成员没有对应记录:v2 不应出现,记为跳过而不是中止。
			skipped++
			continue
		}
		value := parseV2Account(token, record)
		if value.DeletedAt != 0 {
			skipped++
			continue
		}
		accounts = append(accounts, value)
	}
	return accounts, skipped, nil
}

// LoadConfig 读取 config:user 全部用户覆盖项,并单独解析 xai.keys。
func (s *Source) LoadConfig(ctx context.Context) (map[string]string, []V2XAIKey, error) {
	fields, err := s.reader.HGetAll(ctx, v2KeyConfigUser)
	if err != nil {
		return nil, nil, fmt.Errorf("读取 v2 配置: %w", err)
	}
	keys, err := parseXAIKeys(fields[v2ConfigXAIKeys])
	if err != nil {
		return nil, nil, err
	}
	return fields, keys, nil
}

// parseV2Account 将 v2 HASH 字段转换为结构化记录。
// v2 的空值统一是空字符串,数字与 JSON 字段都需要按空串即缺失处理。
func parseV2Account(token string, record map[string]string) V2Account {
	value := V2Account{
		Token:       token,
		Pool:        strings.TrimSpace(record["pool"]),
		Status:      strings.TrimSpace(record["status"]),
		Tags:        parseV2Tags(record["tags"]),
		CreatedAt:   parseV2Int(record["created_at"]),
		UpdatedAt:   parseV2Int(record["updated_at"]),
		LastUseAt:   parseV2Int(record["last_use_at"]),
		DeletedAt:   parseV2Int(record["deleted_at"]),
		UsageUse:    parseV2Int(record["usage_use_count"]),
		UsageFail:   parseV2Int(record["usage_fail_count"]),
		UsageSync:   parseV2Int(record["usage_sync_count"]),
		LastFailAt:  parseV2Int(record["last_fail_at"]),
		LastFail:    record["last_fail_reason"],
		LastSyncAt:  parseV2Int(record["last_sync_at"]),
		StateReason: record["state_reason"],
		Ext:         strings.TrimSpace(record["ext"]),
		Revision:    parseV2Int(record["revision"]),
	}
	quota := make(map[string]string)
	for _, window := range []string{"auto", "fast", "expert", "heavy"} {
		if raw := strings.TrimSpace(record["quota_"+window]); raw != "" && raw != "{}" {
			quota[window] = raw
		}
	}
	if len(quota) > 0 {
		value.Quota = quota
	}
	return value
}

func parseV2Tags(raw string) []string {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return nil
	}
	var tags []string
	if err := json.Unmarshal([]byte(raw), &tags); err != nil {
		return nil
	}
	return tags
}

func parseV2Int(raw string) int64 {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return 0
	}
	value, err := strconv.ParseInt(raw, 10, 64)
	if err != nil {
		return 0
	}
	return value
}

// parseXAIKeys 解析配置 field xai.keys(整个数组的 JSON 文本)。
func parseXAIKeys(raw string) ([]V2XAIKey, error) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return nil, nil
	}
	var keys []V2XAIKey
	if err := json.Unmarshal([]byte(raw), &keys); err != nil {
		return nil, fmt.Errorf("解析 v2 xai.keys: %w", err)
	}
	result := keys[:0]
	for _, key := range keys {
		if strings.TrimSpace(key.Key) != "" {
			result = append(result, key)
		}
	}
	return result, nil
}
