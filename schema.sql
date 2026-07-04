-- =============================================================================
-- GEO モニタリング SaaS -- PostgreSQL スキーマ定義
-- Sprint 1 | 作成: 2026-07-03
-- =============================================================================

-- uuid_generate_v4() を使うために拡張を有効化
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- =============================================================================
-- 1. clients（クライアント）
-- =============================================================================
CREATE TABLE clients (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT        NOT NULL,
    -- ブランド検索キーワード（英語・カタカナ両方を格納 -> 言及判定プロンプトへ渡す）
    brand_keywords  TEXT[]      NOT NULL DEFAULT '{}',
    -- 競合ブランドキーワード
    competitor_keywords TEXT[]  NOT NULL DEFAULT '{}',
    -- Sheets 出力先
    spreadsheet_id  TEXT,
    -- バッチ実行設定
    run_frequency   TEXT        NOT NULL DEFAULT 'monthly'
                                CHECK (run_frequency IN ('weekly', 'monthly')),
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- 2. query_sets（クエリセット）
-- =============================================================================
CREATE TABLE query_sets (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       UUID        NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    prompt_text     TEXT        NOT NULL,
    -- 初期検討 / 比較検討 / 費用予算 / 会場詳細 / 意思決定
    category        TEXT        NOT NULL,
    sort_order      INT         NOT NULL DEFAULT 0,
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_query_sets_client_id   ON query_sets(client_id);
CREATE INDEX idx_query_sets_is_active   ON query_sets(client_id, is_active);

-- =============================================================================
-- 3. ai_responses（AI 回答ログ）
-- =============================================================================
CREATE TABLE ai_responses (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    query_set_id    UUID        NOT NULL REFERENCES query_sets(id) ON DELETE CASCADE,
    ai_provider     TEXT        NOT NULL
                                CHECK (ai_provider IN ('chatgpt','gemini','perplexity','ai_overview')),
    response_text   TEXT        NOT NULL DEFAULT '',   -- 空文字 = 検出なし（AI Overview等）
    model_version   TEXT,                              -- "gpt-4o-mini", "gemini-1.5-flash" 等
    detected        BOOLEAN     NOT NULL DEFAULT TRUE, -- AI Overview 未検出時は FALSE
    retrieved_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    raw_data        JSONB                              -- 生レスポンス全体（デバッグ用）
);

CREATE INDEX idx_ai_responses_query_set   ON ai_responses(query_set_id);
CREATE INDEX idx_ai_responses_provider    ON ai_responses(ai_provider);
CREATE INDEX idx_ai_responses_retrieved   ON ai_responses(retrieved_at DESC);

-- =============================================================================
-- 4. mention_analysis（言及分析結果）
-- =============================================================================
CREATE TABLE mention_analysis (
    id                   UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    ai_response_id       UUID    NOT NULL REFERENCES ai_responses(id) ON DELETE CASCADE,
    -- 言及有無・種別
    is_mentioned         BOOLEAN NOT NULL DEFAULT FALSE,
    mention_type         TEXT    CHECK (mention_type IN ('brand_name','url','both','none')),
    -- トーン
    sentiment            TEXT    CHECK (sentiment IN ('positive','neutral','negative','not_applicable')),
    sentiment_reason     TEXT    DEFAULT '',
    -- 引用元 URL
    cited_urls           TEXT[]  NOT NULL DEFAULT '{}',
    -- 競合言及（[{"name":"XX","is_mentioned":true,"sentiment":"positive"}]）
    competitor_mentions  JSONB   NOT NULL DEFAULT '[]',
    -- 誤情報
    misinformation_flag  BOOLEAN NOT NULL DEFAULT FALSE,
    misinformation_detail TEXT   DEFAULT '',
    -- 言及箇所の抜粋（最大 200 字）
    raw_mention_excerpt  TEXT    DEFAULT '',
    analyzed_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_mention_is_mentioned  ON mention_analysis(is_mentioned);
CREATE INDEX idx_mention_sentiment     ON mention_analysis(sentiment);
-- ai_response_id は 1:1 のはずだが念のため
CREATE UNIQUE INDEX idx_mention_response_unique ON mention_analysis(ai_response_id);

-- =============================================================================
-- 5. reports（月次レポート）
-- =============================================================================
CREATE TABLE reports (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       UUID        NOT NULL REFERENCES clients(id),
    period          TEXT        NOT NULL,  -- "2026-07"
    mention_rate    NUMERIC(5,2),          -- 全体言及率 (%)
    summary_text    TEXT,                  -- Claude 生成サマリー
    sheets_url      TEXT,                  -- 出力先 Sheets URL
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (client_id, period)
);

-- =============================================================================
-- 6. updated_at 自動更新トリガー
-- =============================================================================
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_clients_updated_at
    BEFORE UPDATE ON clients
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_query_sets_updated_at
    BEFORE UPDATE ON query_sets
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- =============================================================================
-- 7. KPI 集計ビュー（月次ダッシュボード用）
-- =============================================================================
CREATE OR REPLACE VIEW v_mention_rate_monthly AS
SELECT
    c.id                                        AS client_id,
    c.name                                      AS client_name,
    ar.ai_provider,
    qs.category,
    TO_CHAR(ar.retrieved_at, 'YYYY-MM')         AS period,
    COUNT(*)                                    AS total_queries,
    COUNT(*) FILTER (WHERE ma.is_mentioned)     AS mentioned_count,
    ROUND(
        COUNT(*) FILTER (WHERE ma.is_mentioned)::NUMERIC
        / NULLIF(COUNT(*), 0) * 100, 1
    )                                           AS mention_rate_pct,
    COUNT(*) FILTER (WHERE ma.sentiment = 'positive')   AS positive_count,
    COUNT(*) FILTER (WHERE ma.sentiment = 'neutral')    AS neutral_count,
    COUNT(*) FILTER (WHERE ma.sentiment = 'negative')   AS negative_count,
    COUNT(*) FILTER (WHERE ma.misinformation_flag)      AS misinformation_count
FROM clients c
JOIN query_sets  qs ON qs.client_id = c.id
JOIN ai_responses ar ON ar.query_set_id = qs.id
LEFT JOIN mention_analysis ma ON ma.ai_response_id = ar.id
WHERE qs.is_active AND c.is_active
GROUP BY c.id, c.name, ar.ai_provider, qs.category, period;

COMMENT ON VIEW v_mention_rate_monthly IS
    'AI別・カテゴリ別・月別の言及率集計ビュー。Google Sheets KPI シートの元データ。';
