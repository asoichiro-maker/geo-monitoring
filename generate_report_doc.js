/**
 * generate_report_doc.js
 * GEO モニタリング 月次レポート生成（docx-js）
 * 使い方: node generate_report_doc.js report_data.json output.docx
 */

const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, HeadingLevel, BorderStyle, WidthType,
  ShadingType, VerticalAlign, PageNumber, PageBreak, LevelFormat,
  ExternalHyperlink,
} = require("docx");
const fs = require("fs");

const dataPath = process.argv[2] || "report_data.json";
const outPath  = process.argv[3] || "GEO_Report.docx";
const data = JSON.parse(fs.readFileSync(dataPath, "utf-8"));

// ── カラー定数 ──────────────────────────────────────────────────────────────
const C = {
  primary:   "1A3A5C",  // ネイビー
  accent:    "C9A84C",  // ゴールド
  light:     "EAF0F6",  // 薄青
  white:     "FFFFFF",
  gray:      "666666",
  lightGray: "F5F5F5",
  red:       "C00000",
};

// ── ユーティリティ ──────────────────────────────────────────────────────────
const border = (color = "CCCCCC") => ({
  style: BorderStyle.SINGLE, size: 1, color,
});
const cellBorders = (color = "DDDDDD") => ({
  top: border(color), bottom: border(color),
  left: border(color), right: border(color),
});
const noBorder = () => ({
  top:    { style: BorderStyle.NONE },
  bottom: { style: BorderStyle.NONE },
  left:   { style: BorderStyle.NONE },
  right:  { style: BorderStyle.NONE },
});

function spacer(pt = 12) {
  return new Paragraph({ children: [new TextRun("")], spacing: { before: pt * 20, after: 0 } });
}

function heading1(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    children: [new TextRun({ text, font: "Arial", size: 32, bold: true, color: C.primary })],
    spacing: { before: 400, after: 200 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: C.accent, space: 1 } },
  });
}

function heading2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    children: [new TextRun({ text, font: "Arial", size: 26, bold: true, color: C.primary })],
    spacing: { before: 280, after: 140 },
  });
}

function bodyText(text, opts = {}) {
  return new Paragraph({
    children: [new TextRun({ text, font: "Arial", size: 22, color: opts.color || "333333", bold: opts.bold || false })],
    spacing: { before: 60, after: 60 },
  });
}

// ── プロバイダー名変換 ───────────────────────────────────────────────────────
function providerLabel(p) {
  return { chatgpt: "ChatGPT", gemini: "Gemini", perplexity: "Perplexity", ai_overview: "AI Overview" }[p] || p;
}

// ── KPI テーブル ─────────────────────────────────────────────────────────────
function kpiTable(mentionRate) {
  const providers = ["chatgpt", "gemini", "perplexity", "ai_overview"];
  const COL = [2800, 2200, 2200, 2160]; // sum = 9360

  const headerRow = new TableRow({
    tableHeader: true,
    children: ["AIプロバイダー", "言及率", "言及クエリ数", "総クエリ数"].map((t, i) =>
      new TableCell({
        borders: cellBorders(C.primary),
        width: { size: COL[i], type: WidthType.DXA },
        shading: { fill: C.primary, type: ShadingType.CLEAR },
        margins: { top: 100, bottom: 100, left: 150, right: 150 },
        verticalAlign: VerticalAlign.CENTER,
        children: [new Paragraph({
          alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: t, font: "Arial", size: 20, bold: true, color: C.white })],
        })],
      })
    ),
  });

  const dataRows = providers.map((p, idx) => {
    const s = mentionRate[p] || { mention_rate: 0, total: 0, mentioned: 0 };
    const rate = (s.mention_rate * 100).toFixed(1) + "%";
    const fill = idx % 2 === 0 ? C.white : C.lightGray;
    return new TableRow({
      children: [
        providerLabel(p),
        rate,
        String(s.mentioned),
        String(s.total),
      ].map((t, i) =>
        new TableCell({
          borders: cellBorders(),
          width: { size: COL[i], type: WidthType.DXA },
          shading: { fill, type: ShadingType.CLEAR },
          margins: { top: 80, bottom: 80, left: 150, right: 150 },
          verticalAlign: VerticalAlign.CENTER,
          children: [new Paragraph({
            alignment: i === 0 ? AlignmentType.LEFT : AlignmentType.CENTER,
            children: [new TextRun({ text: t, font: "Arial", size: 20, color: "333333" })],
          })],
        })
      ),
    });
  });

  return new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: COL,
    rows: [headerRow, ...dataRows],
  });
}

// ── 誤情報テーブル ───────────────────────────────────────────────────────────
function misinfoTable(misinfoRows) {
  const COL = [1800, 5760, 1800]; // sum = 9360
  const headerRow = new TableRow({
    tableHeader: true,
    children: ["AI", "検出内容（要約）", "クエリ"].map((t, i) =>
      new TableCell({
        borders: cellBorders(C.red),
        width: { size: COL[i], type: WidthType.DXA },
        shading: { fill: C.red, type: ShadingType.CLEAR },
        margins: { top: 80, bottom: 80, left: 150, right: 150 },
        children: [new Paragraph({
          alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: t, font: "Arial", size: 19, bold: true, color: C.white })],
        })],
      })
    ),
  });

  const rows = misinfoRows.slice(0, 20).map((r, idx) => {
    const fill = idx % 2 === 0 ? C.white : "FFF5F5";
    const detail = (r.misinformation_detail || "").substring(0, 120) + (r.misinformation_detail?.length > 120 ? "…" : "");
    const query  = (r.prompt_text || "").substring(0, 30) + (r.prompt_text?.length > 30 ? "…" : "");
    return new TableRow({
      children: [providerLabel(r.ai_provider), detail, query].map((t, i) =>
        new TableCell({
          borders: cellBorders("FFCCCC"),
          width: { size: COL[i], type: WidthType.DXA },
          shading: { fill, type: ShadingType.CLEAR },
          margins: { top: 60, bottom: 60, left: 150, right: 150 },
          children: [new Paragraph({
            children: [new TextRun({ text: t, font: "Arial", size: 18, color: "333333" })],
          })],
        })
      ),
    });
  });

  return new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: COL,
    rows: [headerRow, ...rows],
  });
}

// ── メインドキュメント ────────────────────────────────────────────────────────
const { year, month, mention_rate, misinfo_rows, spreadsheet_url } = data;
const periodLabel = `${year}年${month}月`;
const misinfoList = (misinfo_rows || []).filter(r => r.misinformation_flag);
const totalQueries = Object.values(mention_rate || {}).reduce((s, v) => s + (v.total || 0), 0);
const totalMentioned = Object.values(mention_rate || {}).reduce((s, v) => s + (v.mentioned || 0), 0);
const overallRate = totalQueries > 0 ? (totalMentioned / totalQueries * 100).toFixed(1) : "0.0";

const doc = new Document({
  styles: {
    default: { document: { run: { font: "Arial", size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 32, bold: true, font: "Arial", color: C.primary },
        paragraph: { spacing: { before: 400, after: 200 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 26, bold: true, font: "Arial", color: C.primary },
        paragraph: { spacing: { before: 280, after: 140 }, outlineLevel: 1 } },
    ],
  },
  numbering: {
    config: [
      { reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•",
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
    ],
  },
  sections: [{
    properties: {
      page: {
        size: { width: 11906, height: 16838 }, // A4
        margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
      },
    },
    headers: {
      default: new Header({
        children: [new Paragraph({
          children: [
            new TextRun({ text: "GEO モニタリングレポート", font: "Arial", size: 18, color: C.gray }),
            new TextRun({ text: `　|　Dears Wedding (Arluis)　|　${periodLabel}`, font: "Arial", size: 18, color: C.gray }),
          ],
          border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: C.accent, space: 1 } },
        })],
      }),
    },
    footers: {
      default: new Footer({
        children: [new Paragraph({
          alignment: AlignmentType.CENTER,
          children: [
            new TextRun({ text: "Confidential　|　", font: "Arial", size: 16, color: C.gray }),
            new TextRun({ text: "Page ", font: "Arial", size: 16, color: C.gray }),
            new TextRun({ children: [PageNumber.CURRENT], font: "Arial", size: 16, color: C.gray }),
          ],
          border: { top: { style: BorderStyle.SINGLE, size: 4, color: C.accent, space: 1 } },
        })],
      }),
    },
    children: [
      // ── 表紙 ──
      spacer(40),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [new TextRun({ text: "GEO モニタリングレポート", font: "Arial", size: 52, bold: true, color: C.primary })],
        spacing: { before: 1000, after: 200 },
      }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [new TextRun({ text: periodLabel, font: "Arial", size: 40, color: C.accent, bold: true })],
        spacing: { before: 0, after: 400 },
      }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        border: { bottom: { style: BorderStyle.SINGLE, size: 8, color: C.accent, space: 1 } },
        children: [new TextRun("")],
        spacing: { before: 0, after: 400 },
      }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [new TextRun({ text: "Dears Wedding　（運営：Arluis）", font: "Arial", size: 26, color: C.gray })],
        spacing: { before: 400, after: 100 },
      }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [new TextRun({ text: "AI言及率・誤情報モニタリング", font: "Arial", size: 22, color: C.gray })],
        spacing: { before: 0, after: 200 },
      }),
      new Paragraph({ children: [new PageBreak()] }),

      // ── エグゼクティブサマリー ──
      heading1("エグゼクティブサマリー"),
      bodyText(`調査期間：${periodLabel}`),
      bodyText(`対象AIプラットフォーム：ChatGPT・Gemini・Perplexity・AI Overview（4社）`),
      spacer(8),
      bodyText(`総合言及率：${overallRate}%（${totalMentioned} / ${totalQueries} クエリ）`, { bold: true }),
      bodyText(`誤情報検出件数：${misinfoList.length}件`, { bold: true, color: misinfoList.length > 0 ? C.red : "333333" }),
      spacer(16),

      // ── サマリーKPIボックス（テーブル流用） ──
      new Table({
        width: { size: 9360, type: WidthType.DXA },
        columnWidths: [2340, 2340, 2340, 2340],
        rows: [
          new TableRow({
            children: ["chatgpt","gemini","perplexity","ai_overview"].map(p => {
              const s = mention_rate[p] || { mention_rate: 0, total: 0 };
              const rate = (s.mention_rate * 100).toFixed(1) + "%";
              return new TableCell({
                borders: cellBorders(C.accent),
                width: { size: 2340, type: WidthType.DXA },
                shading: { fill: C.light, type: ShadingType.CLEAR },
                margins: { top: 120, bottom: 120, left: 150, right: 150 },
                verticalAlign: VerticalAlign.CENTER,
                children: [
                  new Paragraph({
                    alignment: AlignmentType.CENTER,
                    children: [new TextRun({ text: providerLabel(p), font: "Arial", size: 19, bold: true, color: C.primary })],
                  }),
                  new Paragraph({
                    alignment: AlignmentType.CENTER,
                    children: [new TextRun({ text: rate, font: "Arial", size: 36, bold: true, color: C.accent })],
                  }),
                  new Paragraph({
                    alignment: AlignmentType.CENTER,
                    children: [new TextRun({ text: `${s.total || 0}クエリ`, font: "Arial", size: 17, color: C.gray })],
                  }),
                ],
              });
            }),
          }),
        ],
      }),

      spacer(20),
      heading1("1. AI別 言及率 詳細"),
      bodyText("各AIプラットフォームにおける Dears Wedding（Arluis）の言及状況です。"),
      spacer(12),
      kpiTable(mention_rate),

      spacer(20),
      heading1("2. 誤情報アラート"),
      bodyText(`今月は ${misinfoList.length}件 の誤情報が検出されました。`
        + (misinfoList.length > 0 ? "早急な対策が推奨されます。" : "")),
      spacer(12),
      ...(misinfoList.length > 0
        ? [misinfoTable(misinfoList)]
        : [bodyText("今月の誤情報検出はありませんでした。", { color: "2E8B57" })]
      ),

      spacer(20),
      heading1("3. 所見と推奨アクション"),
      heading2("現状分析"),
      ...[
        `総合言及率 ${overallRate}% は、ブランド認知がAI回答に反映されている割合を示します。`,
        `誤情報として最も多いパターン：「東京都内専門・少人数20名以下」に反する記述（全国展開・大規模披露宴対応など）。`,
        `Gemini・Perplexityでは他社ブランド（リゾートウェディング系）と混同される傾向が見られます。`,
      ].map(t => new Paragraph({
        numbering: { reference: "bullets", level: 0 },
        children: [new TextRun({ text: t, font: "Arial", size: 21, color: "333333" })],
        spacing: { before: 60, after: 60 },
      })),

      spacer(12),
      heading2("推奨アクション"),
      ...[
        "【高優先度】公式サイトのコンテンツを「東京都内・少人数20名以下専門」と明示するページを強化し、AIクローラーに正確な情報を提供する。",
        "【中優先度】誤情報が多いクエリカテゴリ（エリア・規模）に対し、Q&A形式のFAQページを追加する。",
        "【継続モニタリング】来月も同クエリセットで再計測し、言及率の推移をトラッキングする。",
      ].map(t => new Paragraph({
        numbering: { reference: "bullets", level: 0 },
        children: [new TextRun({ text: t, font: "Arial", size: 21, color: "333333" })],
        spacing: { before: 60, after: 60 },
      })),

      spacer(20),
      heading1("4. データソース"),
      bodyText("詳細データはGoogle Sheetsで確認できます："),
      spacer(8),
      new Paragraph({
        children: [new ExternalHyperlink({
          children: [new TextRun({ text: spreadsheet_url || "(URL未設定)", style: "Hyperlink", font: "Arial", size: 21 })],
          link: spreadsheet_url || "#",
        })],
      }),
      spacer(40),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [new TextRun({ text: "— 以上 —", font: "Arial", size: 20, color: C.gray })],
      }),
    ],
  }],
});

Packer.toBuffer(doc).then(buf => {
  fs.writeFileSync(outPath, buf);
  console.log("Generated:", outPath);
}).catch(e => { console.error(e); process.exit(1); });
