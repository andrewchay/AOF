#!/usr/bin/env python3
"""检索增益评测文档集生成（领域：零售 CRM / 会员 / 积分）。

生成 6 份含表格、标题层级、中英混排、实体关系的 PDF 文档。
每份围绕独立主题（会员/积分/KPOS/商圈/营销/数据），便于构造 ground-truth 查询集。

输出：tools/retrieval_gain/dataset/*.pdf
"""

from __future__ import annotations

import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "dataset"


def _pdf(fname: str, title: str, sections: list[tuple[str, str]], table: list[list[str]] | None = None):
    """生成单份中文 PDF：标题 + 章节 + 可选表格。"""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    base = "Helvetica"
    for fp in [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
    ]:
        try:
            pdfmetrics.registerFont(TTFont("CJK", fp))
            base = "CJK"
            break
        except Exception:
            continue

    title_st = ParagraphStyle("t", parent=ParagraphStyle("x"), fontName=base, fontSize=20, leading=26)
    h_st = ParagraphStyle("h", parent=ParagraphStyle("x"), fontName=base, fontSize=14, leading=20)
    b_st = ParagraphStyle("b", parent=ParagraphStyle("x"), fontName=base, fontSize=11, leading=16, wordWrap="CJK")

    story = [Paragraph(title, title_st), Spacer(1, 5 * mm)]
    for h, body in sections:
        story.append(Paragraph(h, h_st))
        story.append(Paragraph(body, b_st))
        story.append(Spacer(1, 3 * mm))

    if table:
        tbl = Table(table, colWidths=[35 * mm] * len(table[0]))
        tbl.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), base),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D9E2F3")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story += [Spacer(1, 3 * mm), tbl]

    OUT.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(str(OUT / fname), pagesize=A4).build(story)
    print(f"  [OK] {fname}")


def main() -> None:
    # 文档1：会员体系 KLUB
    _pdf(
        "member_klub.pdf", "KLUB 会员体系设计",
        [
            ("一、会员分层", "KLUB 体系共分银卡、金卡、黑卡三个层级，依据年度消费额自动升降。银卡门槛 3 千元，金卡 2 万元，黑卡 10 万元。"),
            ("二、权益差异", "黑卡会员享有专属客服、生日双倍积分与机场贵宾厅权益；金卡会员享免费停车与季节赠礼；银卡享基础积分返利。"),
        ],
        [["层级", "门槛(元)", "积分倍数", "核心权益"], ["银卡", "3000", "1x", "基础返利"], ["金卡", "20000", "1.5x", "停车/赠礼"], ["黑卡", "100000", "2x", "专属客服/贵宾厅"]],
    )
    # 文档2：积分体系
    _pdf(
        "points_system.pdf", "积分累积与兑换规则",
        [
            ("一、累积规则", "消费 1 元积 1 分，微信支付额外 10% 积分奖励。积分有效期为 24 个月，逾期清零。"),
            ("二、兑换规则", "100 积分可抵 1 元现金，积分可兑换停车券、超市券与里程。黑卡会员兑换比例上浮 20%。"),
            ("三、防盗刷", "同一微信支付流水号仅累计一次积分布防重复。KPOS 交易需校验微信支付流水号避免重复积分。"),
        ],
        [["行为", "积分奖励", "说明"], ["消费", "1元/分", "基础累积"], ["微信支付", "+10%", "额外奖励"], ["生日当天", "双倍", "仅限本人"]],
    )
    # 文档3：KPOS 门店系统
    _pdf(
        "kpos_store.pdf", "KPOS 门店交易系统",
        [
            ("一、系统概述", "KPOS 是门店收银与积分一体化终端，记录 KPOS 订单号并支持微信支付。部分银行（如中国银行）可返回微信支付流水号。"),
            ("二、防重复积分", "由于 KPOS 记录订单号而非支付流水号，与微信商圈同时开通会导致重复积分。门店只能二选一开通。"),
            ("三、升级方向", "若要同时开通 KPOS 与微信商圈，需 KPOS 交易提供微信支付流水号，待与其它银行确认支持情况。"),
        ],
    )
    # 文档4：微信商圈
    _pdf(
        "wechat_circle.pdf", "微信商圈积分打通",
        [
            ("一、接入方式", "商户通过微信支付商圈接口接入，消费即积分，每笔交易记录微信支付流水号作为积分依据。"),
            ("二、自助积分", "小额客（KGO）可通过自助扫描上传小票积分，系统加入 OCR 识别支付方式，微信支付/未知时特殊处理防止重复。"),
            ("三、数据校验", "积分前会校验微信支付流水号是否已累积，防止同一流水号重复积分。"),
        ],
        [["渠道", "积分依据", "防重机制"], ["微信商圈", "支付流水号", "流水号去重"], ["KGO自助", "小票OCR", "OCR+去重"], ["KPOS", "订单号", "无法防重"]],
    )
    # 文档5：七夕营销活动
    _pdf(
        "qixi_campaign.pdf", "七夕会员营销活动",
        [
            ("一、活动总览", "七夕期间（7.22-8.7）推出会员小K盒抢先购与满额赠礼。小K盒提前锁定 Mall Sales，满额赠礼按实付金额分档。"),
            ("二、规则", "7.22-8.7 会员通过 KGO 购买小K盒；满 500 元赠 50 元券，满 1000 元赠 150 元券。young couple 定位优先，红色文字表示系统不可设置。"),
            ("三、数据表现", "活动带动会员消费环比增长 18%，积分兑换率提升 6 个百分点。"),
        ],
        [["档位", "满额(元)", "赠礼"], ["一档", "500", "50元券"], ["二档", "1000", "150元券"]],
    )
    # 文档6：分 site 运营数据
    _pdf(
        "site_data.pdf", "分站点核心运营数据",
        [
            ("一、数据概览", "上海与广州为会员规模最大站点，上海 CRM_USER 约 49621，广州超过 10 万。会员月度消费频次 1.2-1.5 次。"),
            ("二、留存率", "黑卡会员 3 个月留存率超 60%，高购（high-gold）会员留存率约 65%，普通会员留存率仅 15%。"),
            ("三、ARPU", "黑卡 ARPU 最高，上海黑卡 ARPU 超 16 万，广州普通会员 ARPU 仅 1194 元。"),
        ],
        [["城市", "黑卡会员", "黑卡留存率", "黑卡ARPU"], ["上海", "925", "62.4%", "161905"], ["广州", "529", "82.6%", "58150"], ["天津", "0", "-", "-"]],
    )


if __name__ == "__main__":
    sys.exit(main())
