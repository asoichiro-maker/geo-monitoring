#!/usr/bin/env python3
"""
consultation_slides.py — GEO改善コンサルレポート生成（PowerPointファイル）

使い方:
    python consultation_slides.py           # テスト（サンプルデータ）
    analyze.py から自動呼び出しも可
"""
from __future__ import annotations
import json, os, subprocess, tempfile, sys

_POSSIBLE_PPTX = [
    "/tmp/pptx_env/node_modules",
    os.path.expanduser("~/AppData/Roaming/npm/node_modules"),
    "/usr/local/lib/node_modules",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "node_modules"),
]
PPTX_NODE_MODULES = next(
    (p for p in _POSSIBLE_PPTX if os.path.isdir(os.path.join(p, "pptxgenjs"))),
    "/tmp/pptx_env/node_modules",
)

# rezip script for smaller file size
REZIP_PY = next(
    (p for p in [
        "/sessions/kind-sharp-cori/mnt/.claude/skills/pptx/scripts/rezip.py",
        r"C:\Users\akimo\AppData\Roaming\Claude\local-agent-mode-sessions\skills-plugin\09b2f68e-652c-4102-bacd-06cfd873026c\781a73eb-6926-4530-b9d9-1e501e5c1545\skills\pptx\scripts\rezip.py",
    ] if os.path.exists(p)),
    None,
)

JS_TEMPLATE = """
const pptxgen = require('__NODE_MODULES__/pptxgenjs');
const fs = require('fs');

const data = JSON.parse(fs.readFileSync('__DATA_FILE__', 'utf8'));
const period = data.period;
const results = data.results;

const C = {
  primary:  '1E2761',
  accent:   '2563EB',
  success:  '059669',
  danger:   'DC2626',
  warning:  'D97706',
  gray:     '6B7280',
  darkgray: '1F2937',
  light:    'F1F5F9',
  white:    'FFFFFF',
};
const BRAND_COLORS = ['1E3A8A', '059669', '7C3AED'];
const PROVIDERS = ['chatgpt', 'gemini', 'perplexity', 'ai_overview'];
const PLABELS = { chatgpt:'ChatGPT', gemini:'Gemini', perplexity:'Perplexity', ai_overview:'AI Overview' };

function rateColor(rate) {
  if (rate >= 30) return C.success;
  if (rate >= 15) return C.warning;
  return C.danger;
}
function makeShadow() {
  return { type: 'outer', color: '000000', blur: 5, offset: 2, angle: 45, opacity: 0.12 };
}
function pKey(p) { return p === '高' ? 'high' : p === '中' ? 'mid' : 'low'; }

const PC = { high: C.danger, mid: C.warning, low: C.success };
const PB = { high: 'FEF2F2', mid: 'FFFBEB', low: 'F0FDF4' };

let pres = new pptxgen();
pres.layout = 'LAYOUT_16x9';
pres.title = 'GEO Monitoring ' + period;

// ──────────────────────────────────────────────────────────────────
// スライド 1: 表紙
// ──────────────────────────────────────────────────────────────────
(function() {
  let slide = pres.addSlide();
  slide.background = { color: C.primary };

  slide.addText('GEO Monitoring', {
    x: 0.5, y: 0.9, w: 9, h: 1.1,
    fontSize: 56, bold: true, color: 'CADCFC', fontFace: 'Calibri',
    align: 'center', valign: 'middle',
  });
  slide.addText('改善コンサルテーション', {
    x: 0.5, y: 2.1, w: 9, h: 0.6,
    fontSize: 24, color: C.white, fontFace: 'Calibri', align: 'center',
  });
  slide.addText(period + '  月次レポート', {
    x: 0.5, y: 2.75, w: 9, h: 0.45,
    fontSize: 17, color: 'BFDBFE', fontFace: 'Calibri', align: 'center',
  });

  // Thin separator
  slide.addShape(pres.shapes.RECTANGLE, {
    x: 3.5, y: 3.35, w: 3, h: 0.04,
    fill: { color: C.accent }, line: { color: C.accent, width: 0 },
  });

  results.forEach(function(r, i) {
    const bc = BRAND_COLORS[i % BRAND_COLORS.length];
    slide.addText(String.fromCharCode(9670) + '  ' + r.brand_name, {
      x: 0.5, y: 3.55 + i * 0.5, w: 9, h: 0.45,
      fontSize: 16, bold: true, color: bc, fontFace: 'Calibri', align: 'center',
    });
  });

  slide.addText('Confidential  |  作成日: ' + new Date().toLocaleDateString('ja-JP'), {
    x: 0.5, y: 5.2, w: 9, h: 0.3,
    fontSize: 10, color: '6B7280', fontFace: 'Calibri', align: 'center',
  });
})();

// ──────────────────────────────────────────────────────────────────
// スライド 2: 全社KPI比較
// ──────────────────────────────────────────────────────────────────
(function() {
  let slide = pres.addSlide();
  slide.background = { color: C.white };

  slide.addText('全社パフォーマンス比較', {
    x: 0.4, y: 0.2, w: 9.2, h: 0.55,
    fontSize: 22, bold: true, color: C.primary, fontFace: 'Calibri',
  });

  const n = results.length;
  const provColW = 2.2;
  const brandColW = parseFloat(((9.2 - provColW) / n).toFixed(3));
  const lastBrandW = parseFloat((9.2 - provColW - brandColW * (n - 1)).toFixed(3));
  const colW = [provColW].concat(results.map(function(_, i) {
    return i < n - 1 ? brandColW : lastBrandW;
  }));

  const headerRow = [
    { text: 'AI プロバイダー', options: { fill: { color: C.primary }, color: C.white, bold: true, fontSize: 13, fontFace: 'Calibri' } }
  ].concat(results.map(function(r, i) {
    return { text: r.brand_name, options: { fill: { color: BRAND_COLORS[i % BRAND_COLORS.length] }, color: C.white, bold: true, fontSize: 12, align: 'center', fontFace: 'Calibri' } };
  }));

  const dataRows = PROVIDERS.map(function(p) {
    return [
      { text: PLABELS[p] || p, options: { fill: { color: 'F8FAFC' }, bold: true, fontSize: 13, color: C.darkgray, fontFace: 'Calibri' } }
    ].concat(results.map(function(r) {
      const d = (r.rates && r.rates[p]) || { rate: 0, mentioned: 0, total: 0 };
      const rate = parseFloat(d.rate) || 0;
      return {
        text: [
          { text: rate.toFixed(1) + '%', options: { bold: true, fontSize: 20, breakLine: true } },
          { text: (d.mentioned || 0) + ' / ' + (d.total || 0) + ' 件', options: { fontSize: 11, color: 'E0F2FE' } },
        ],
        options: { fill: { color: rateColor(rate) }, color: C.white, align: 'center', valign: 'middle', fontFace: 'Calibri' }
      };
    }));
  });

  slide.addTable([headerRow].concat(dataRows), {
    x: 0.4, y: 0.88, w: 9.2,
    colW: colW,
    rowH: [0.65, 0.95, 0.95, 0.95, 0.95],
    border: { pt: 2, color: C.white },
    fontFace: 'Calibri',
  });

  // Legend
  slide.addText([
    { text: String.fromCharCode(9632) + ' ', options: { color: C.success, fontSize: 10 } },
    { text: '30%以上（良好）   ', options: { color: C.gray, fontSize: 10 } },
    { text: String.fromCharCode(9632) + ' ', options: { color: C.warning, fontSize: 10 } },
    { text: '15〜29%（注意）   ', options: { color: C.gray, fontSize: 10 } },
    { text: String.fromCharCode(9632) + ' ', options: { color: C.danger, fontSize: 10 } },
    { text: '15%未満（要改善）', options: { color: C.gray, fontSize: 10 } },
  ], { x: 0.4, y: 5.25, w: 9.2, h: 0.25, fontFace: 'Calibri' });
})();

// ──────────────────────────────────────────────────────────────────
// 各ブランド（3スライドずつ）
// ──────────────────────────────────────────────────────────────────
results.forEach(function(r, idx) {
  const bc = BRAND_COLORS[idx % BRAND_COLORS.length];
  const a = r.analysis || {};

  // ── スライド A: KPIカード + エグゼクティブサマリー ──
  (function() {
    let slide = pres.addSlide();
    slide.background = { color: C.white };

    slide.addShape(pres.shapes.RECTANGLE, {
      x: 0, y: 0, w: 10, h: 0.72,
      fill: { color: bc }, line: { color: bc, width: 0 },
    });
    slide.addText(r.brand_name, {
      x: 0.4, y: 0, w: 9.2, h: 0.72,
      fontSize: 22, bold: true, color: C.white, fontFace: 'Calibri',
      valign: 'middle', margin: 0,
    });

    // 4 KPI cards
    const cardXs = [0.35, 2.7, 5.05, 7.4];
    const cardW = 2.2, cardH = 1.32, cardY = 0.88;
    PROVIDERS.forEach(function(p, i) {
      const d = (r.rates && r.rates[p]) || { rate: 0, mentioned: 0, total: 0 };
      const rate = parseFloat(d.rate) || 0;
      const color = rateColor(rate);
      const cx = cardXs[i];

      slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
        x: cx, y: cardY, w: cardW, h: cardH,
        fill: { color: color }, rectRadius: 0.08,
        line: { color: color, width: 0 },
        shadow: makeShadow(),
      });
      slide.addText(PLABELS[p], {
        x: cx, y: cardY + 0.06, w: cardW, h: 0.28,
        align: 'center', fontSize: 11, bold: true, color: C.white, fontFace: 'Calibri', margin: 0,
      });
      slide.addText(rate.toFixed(1) + '%', {
        x: cx, y: cardY + 0.32, w: cardW, h: 0.62,
        align: 'center', fontSize: 36, bold: true, color: C.white, fontFace: 'Calibri', margin: 0,
      });
      slide.addText((d.mentioned || 0) + ' / ' + (d.total || 0) + ' 件', {
        x: cx, y: cardY + 0.95, w: cardW, h: 0.28,
        align: 'center', fontSize: 11, color: 'D1FAE5', fontFace: 'Calibri', margin: 0,
      });
    });

    // Executive summary card
    if (a.executive_summary) {
      slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
        x: 0.35, y: 2.38, w: 9.3, h: 3.0,
        fill: { color: 'F0F4FF' }, rectRadius: 0.1,
        line: { color: 'BFDBFE', width: 1 },
      });
      slide.addText('エグゼクティブサマリー', {
        x: 0.5, y: 2.48, w: 9, h: 0.3,
        fontSize: 11, bold: true, color: bc, fontFace: 'Calibri', margin: 0,
      });
      slide.addText(a.executive_summary, {
        x: 0.5, y: 2.82, w: 9.0, h: 2.4,
        fontSize: 13, color: C.darkgray, fontFace: 'Calibri', valign: 'top', margin: 0,
      });
    }
  })();

  // ── スライド B: 強み & 課題 ──
  (function() {
    let slide = pres.addSlide();
    slide.background = { color: C.light };

    slide.addShape(pres.shapes.RECTANGLE, {
      x: 0, y: 0, w: 10, h: 0.62,
      fill: { color: bc }, line: { color: bc, width: 0 },
    });
    slide.addText(r.brand_name + '  —  強み & 課題', {
      x: 0.4, y: 0, w: 9.2, h: 0.62,
      fontSize: 18, bold: true, color: C.white, fontFace: 'Calibri',
      valign: 'middle', margin: 0,
    });

    // Strengths column (left)
    slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x: 0.35, y: 0.78, w: 4.55, h: 4.6,
      fill: { color: C.white }, rectRadius: 0.1,
      line: { color: 'D1FAE5', width: 1 },
      shadow: makeShadow(),
    });
    slide.addText('現状の強み', {
      x: 0.55, y: 0.88, w: 4.15, h: 0.38,
      fontSize: 14, bold: true, color: C.success, fontFace: 'Calibri',
    });
    if (a.strengths && a.strengths.length > 0) {
      const sItems = a.strengths.map(function(s, j) {
        return { text: s, options: { bullet: true, breakLine: j < a.strengths.length - 1, fontSize: 13, paraSpaceAfter: 8, color: C.darkgray } };
      });
      slide.addText(sItems, { x: 0.5, y: 1.32, w: 4.2, h: 3.9, fontFace: 'Calibri', valign: 'top' });
    }

    // Issues column (right)
    slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x: 5.1, y: 0.78, w: 4.55, h: 4.6,
      fill: { color: C.white }, rectRadius: 0.1,
      line: { color: 'FECACA', width: 1 },
      shadow: makeShadow(),
    });
    slide.addText('課題分析', {
      x: 5.3, y: 0.88, w: 4.15, h: 0.38,
      fontSize: 14, bold: true, color: C.danger, fontFace: 'Calibri',
    });
    if (a.issues && a.issues.length > 0) {
      const issueItems = [];
      a.issues.forEach(function(issue, i) {
        issueItems.push({ text: String(i + 1) + '. ' + issue.title, options: { bold: true, fontSize: 13, color: C.danger, breakLine: true, paraSpaceAfter: 2 } });
        issueItems.push({ text: issue.detail, options: { fontSize: 12, color: C.darkgray, breakLine: true, paraSpaceAfter: 4 } });
        if (issue.evidence) {
          issueItems.push({ text: '根拠: ' + issue.evidence, options: { fontSize: 11, color: C.gray, italic: true, breakLine: i < a.issues.length - 1, paraSpaceAfter: 10 } });
        }
      });
      slide.addText(issueItems, { x: 5.25, y: 1.32, w: 4.2, h: 3.9, fontFace: 'Calibri', valign: 'top' });
    }
  })();

  // ── スライド C: アクションプラン ──
  (function() {
    let slide = pres.addSlide();
    slide.background = { color: C.white };

    slide.addShape(pres.shapes.RECTANGLE, {
      x: 0, y: 0, w: 10, h: 0.65,
      fill: { color: bc }, line: { color: bc, width: 0 },
    });
    slide.addText(r.brand_name + '  —  改善アクションプラン', {
      x: 0.4, y: 0, w: 9.2, h: 0.65,
      fontSize: 18, bold: true, color: C.white, fontFace: 'Calibri',
      valign: 'middle', margin: 0,
    });

    // focusボックスがある場合は2件に絞ってスペースを確保
    const maxRecs = a.next_month_focus ? 2 : 4;
    const recs = (a.recommendations || []).slice(0, maxRecs);
    if (recs.length > 0) {
      const headerRow = [
        { text: '優先度', options: { fill: { color: C.primary }, color: C.white, bold: true, align: 'center', fontSize: 12, fontFace: 'Calibri' } },
        { text: 'タスク・実施手順', options: { fill: { color: C.primary }, color: C.white, bold: true, fontSize: 12, fontFace: 'Calibri' } },
        { text: '担当 / 期限 / 期待効果', options: { fill: { color: C.primary }, color: C.white, bold: true, fontSize: 12, fontFace: 'Calibri' } },
      ];

      const rowH = recs.map(function(rec) {
        const steps = (rec.steps || (rec.action ? [rec.action] : [])).slice(0, 3);
        return Math.min(Math.max(0.9, 0.45 + steps.length * 0.45), 1.9);
      });

      const dataRows = recs.map(function(rec, i) {
        const k = pKey(rec.priority);
        const pColor = PC[k] || C.gray;
        const pBg = PB[k] || 'F9FAFB';
        const steps = (rec.steps || (rec.action ? [rec.action] : [])).slice(0, 3);
        const taskTitle = rec.task || rec.action || '';

        const taskItems = [{ text: taskTitle, options: { bold: true, fontSize: 13, color: C.darkgray, breakLine: true, paraSpaceAfter: 4 } }];
        steps.forEach(function(s, si) {
          const sText = s.length > 75 ? s.substring(0, 72) + '...' : s;
          taskItems.push({ text: String.fromCharCode(9658) + ' ' + sText, options: { fontSize: 10, color: C.gray, breakLine: si < steps.length - 1, paraSpaceAfter: 2 } });
        });

        const metaItems = [];
        if (rec.owner) metaItems.push({ text: '担当: ' + rec.owner, options: { fontSize: 11, bold: false, breakLine: true, paraSpaceAfter: 3 } });
        if (rec.timeline) metaItems.push({ text: '期限: ' + rec.timeline, options: { fontSize: 11, bold: true, color: C.darkgray, breakLine: true, paraSpaceAfter: 3 } });
        if (rec.expected_result) {
          metaItems.push({ text: '効果: ', options: { fontSize: 10, bold: true, color: C.success } });
          metaItems.push({ text: rec.expected_result, options: { fontSize: 10, color: C.darkgray, breakLine: true } });
        } else if (rec.rationale) {
          metaItems.push({ text: rec.rationale, options: { fontSize: 10, color: C.gray } });
        }

        return [
          { text: [
              { text: String.fromCharCode(9679), options: { fontSize: 18, color: pColor, breakLine: true } },
              { text: rec.priority || '-', options: { fontSize: 14, bold: true, color: pColor } },
            ],
            options: { fill: { color: pBg }, align: 'center', valign: 'middle', fontFace: 'Calibri' }
          },
          { text: taskItems, options: { valign: 'top', fontFace: 'Calibri' } },
          { text: metaItems.length > 0 ? metaItems : [{ text: '-', options: { color: C.gray } }],
            options: { fill: { color: 'F8FAFC' }, valign: 'top', fontFace: 'Calibri' } },
        ];
      });

      const tableY = 0.8;
      const focusBoxH = 0.82;
      // Reserve space for focus box; hard cap table at maxTableH
      const maxTableH = a.next_month_focus ? 3.9 : 4.6;
      const finalRowH = rowH.slice();
      const rawRowSum = rowH.reduce(function(a, b) { return a + b; }, 0);
      if (rawRowSum + 0.52 > maxTableH) {
        const scale = (maxTableH - 0.52) / rawRowSum;
        for (let i = 0; i < finalRowH.length; i++) {
          finalRowH[i] = Math.round(finalRowH[i] * scale * 100) / 100;
        }
      }

      slide.addTable([headerRow].concat(dataRows), {
        x: 0.35, y: tableY, w: 9.3,
        colW: [0.8, 5.9, 2.6],
        rowH: [0.52].concat(finalRowH),
        border: { pt: 1, color: 'E5E7EB' },
        fontFace: 'Calibri',
      });

      // Next month focus box — always placed right after capped table
      if (a.next_month_focus) {
        const focusY = tableY + maxTableH + 0.1;
        // Truncate to ~90 chars to fit single-line box
        const focusText = a.next_month_focus.length > 90
          ? a.next_month_focus.substring(0, 87) + '...'
          : a.next_month_focus;
        slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
          x: 0.35, y: focusY, w: 9.3, h: focusBoxH,
          fill: { color: 'FFFBEB' }, rectRadius: 0.07,
          line: { color: C.warning, width: 1 },
        });
        slide.addText([
          { text: String.fromCharCode(9733) + '  来月の注目KPI: ', options: { bold: true, color: C.warning } },
          { text: focusText, options: { color: C.darkgray } },
        ], {
          x: 0.5, y: focusY + 0.08, w: 9.0, h: focusBoxH - 0.12,
          fontSize: 11, fontFace: 'Calibri', valign: 'middle', margin: 0,
        });
      }
    }
  })();
});

// ── 出力 ─────────────────────────────────────────────────────────
pres.writeFile({ fileName: '__OUTPUT_FILE__' }).then(function() {
  console.log('OK');
}).catch(function(e) { console.error(e); process.exit(1); });
"""


def generate_slides(period: str, results: list, output_dir: str | None = None) -> str:
    """分析結果からPowerPointスライドを生成。ファイルパスを返す。"""
    if output_dir is None:
        output_dir = os.path.dirname(os.path.abspath(__file__))

    output_file = os.path.join(output_dir, f"geo_consultation_{period}.pptx")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json",
                                     delete=False, encoding="utf-8") as f:
        json.dump({"period": period, "results": results},
                  f, ensure_ascii=False, default=str)
        data_file = f.name

    js_code = (JS_TEMPLATE
               .replace("__NODE_MODULES__", PPTX_NODE_MODULES)
               .replace("__DATA_FILE__", data_file.replace("\\", "/"))
               .replace("__OUTPUT_FILE__", output_file.replace("\\", "/")))

    with tempfile.NamedTemporaryFile(mode="w", suffix=".js",
                                      delete=False, encoding="utf-8") as f:
        f.write(js_code)
        js_file = f.name

    try:
        result = subprocess.run(
            ["node", js_file],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Node.js error: {result.stderr}")
        if not os.path.exists(output_file):
            raise RuntimeError("ファイルが生成されませんでした")

        # ファイルサイズ最適化
        if REZIP_PY and os.path.exists(REZIP_PY):
            subprocess.run(["python", REZIP_PY, output_file],
                           capture_output=True, timeout=30)
    finally:
        os.unlink(data_file)
        os.unlink(js_file)

    return output_file


if __name__ == "__main__":
    import tempfile as _tmp
    sample = [
        {
            "brand_name": "Dears Wedding RESORT",
            "rates": {
                "chatgpt":    {"rate": 20.8, "mentioned": 5, "total": 24},
                "gemini":     {"rate": 20.8, "mentioned": 5, "total": 24},
                "perplexity": {"rate": 16.7, "mentioned": 4, "total": 24},
                "ai_overview":{"rate": 20.8, "mentioned": 5, "total": 24},
            },
            "analysis": {
                "executive_summary": "Dears Wedding RESOORTの総合AI言及率は約19.8%で、競合2社と同水準です。ハワイ・バリ・ダナンといった希少エリアへの対応が差別化ポイントになり得ますが、現状のAI回答ではその優位性が十分に反映されていません。",
                "strengths": ["沖縄・ハワイ・バリ・ダナンの4エリア対応", "中小規模ブランドとしてニッチ訴求が可能"],
                "issues": [
                    {"title": "AI認知度が低い", "detail": "大手2社と比べてAIが推薦する頻度が同等だが、固有情報の反映が少ない", "evidence": "非言及クエリの回答でダナン・バリへの言及がほぼゼロ"},
                ],
                "recommendations": [
                    {
                        "priority": "高",
                        "task": "ダナン専用LPを新規作成",
                        "steps": [
                            "手順1: dearswedding-resort.jp/danang-wedding を新規作成し、H1タグに「ダナン リゾートウエディング チャペル挙式」を設定",
                            "手順2: Schema.org WeddingVenueマークアップをJSON-LD形式でページに追加",
                            "手順3: Google Search ConsoleでURLを検査しインデックス登録リクエストを送信",
                        ],
                        "owner": "技術担当 + コンテンツ担当",
                        "expected_result": "ダナン関連クエリの言及率 0% → 15%以上",
                        "timeline": "翌月まで",
                    },
                    {
                        "priority": "中",
                        "task": "Schema.orgをサイト全体に適用",
                        "steps": [
                            "手順1: トップ・エリア別各ページにWeddingVenueスキーマをJSON-LD形式で追記",
                            "手順2: Googleリッチリザルトテストで全ページを検証",
                        ],
                        "owner": "技術担当",
                        "expected_result": "全体言及率 +3〜5%",
                        "timeline": "3ヶ月以内",
                    },
                ],
                "next_month_focus": "ダナン・バリ関連クエリの言及率と新規LPのGoogleインデックス状況",
            },
        },
        {
            "brand_name": "アールイズ・ウエディング",
            "rates": {
                "chatgpt":    {"rate": 18.1, "mentioned": 4, "total": 22},
                "gemini":     {"rate": 22.7, "mentioned": 5, "total": 22},
                "perplexity": {"rate": 27.3, "mentioned": 6, "total": 22},
                "ai_overview":{"rate": 13.6, "mentioned": 3, "total": 22},
            },
            "analysis": {
                "executive_summary": "アールイズ・ウエディングはPerplexityでの言及率が27.3%と高く、ハワイ・グアム向けのクエリで優位性を示しています。一方AI Overviewでの言及は低く、Googleの生成AI回答への露出が課題です。",
                "strengths": ["Perplexityでの認知が高い", "ハワイ・グアムの豊富な実績"],
                "issues": [
                    {"title": "AI Overview露出が不足", "detail": "GoogleのAI概要に表示される頻度が最も低い", "evidence": "ai_overview言及率13.6%、競合2社より5〜7pt低い"},
                ],
                "recommendations": [
                    {
                        "priority": "高",
                        "task": "Googleビジネスプロフィールを更新",
                        "steps": [
                            "手順1: GBP管理画面でカテゴリに「ウェディングサービス」「リゾートウェディング」を追加",
                            "手順2: ハワイ・グアム会場写真を10枚以上アップ（alt説明文に地名+リゾート婚を含める）",
                            "手順3: Q&Aセクションに「海外挙式 費用目安」「グアム 挙式」などを5件以上投稿",
                        ],
                        "owner": "マーケ担当",
                        "expected_result": "AI Overview言及率 13.6% → 20%以上（+6pt）",
                        "timeline": "2週間以内",
                    },
                ],
                "next_month_focus": "AI Overview言及率の改善幅とグアム向けクエリの推移",
            },
        },
        {
            "brand_name": "ワタベウェディング",
            "rates": {
                "chatgpt":    {"rate": 33.3, "mentioned": 8, "total": 24},
                "gemini":     {"rate": 25.0, "mentioned": 6, "total": 24},
                "perplexity": {"rate": 29.2, "mentioned": 7, "total": 24},
                "ai_overview":{"rate": 20.8, "mentioned": 5, "total": 24},
            },
            "analysis": {
                "executive_summary": "ワタベウェディングはChatGPTで33.3%と3社中最高の言及率を誇ります。自社衣裳ブランドgenicoco・価格帯の幅広さが強みで、全プロバイダーで安定した言及が確認できGEO戦略の基盤が整っています。",
                "strengths": ["ChatGPT言及率33%と業界トップ水準", "自社衣裳ブランドgenicoco保有", "全プロバイダーで安定した言及"],
                "issues": [
                    {"title": "AI Overviewでのシェアが伸びない", "detail": "他プロバイダーと比較してAI Overview言及率が低い", "evidence": "20.8%に留まりChatGPTの33%と13pt差"},
                ],
                "recommendations": [
                    {
                        "priority": "中",
                        "task": "genicocoのFAQページを追加",
                        "steps": [
                            "手順1: watabe-wedding.co.jp/genicoco/faq を新規作成し、Q&A形式で「genicocoとは」「レンタル料金」「取り扱い店舗」を記述",
                            "手順2: FAQPageスキーマ（JSON-LD）をページに追加",
                            "手順3: genicoco トップページからfaqページへの内部リンクを設置",
                        ],
 
                    },
                ],
                "next_month_focus": "KPI追跡",
            },
        },
    ]
    out = generate_slides("2026-07", sample, ".")
    print("Generated:", out)
