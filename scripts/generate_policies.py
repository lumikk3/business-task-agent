#!/usr/bin/env python3
"""建立售后知识库：生成 50~100 份规则文档到 data/policies/。

    python scripts/generate_policies.py
    python scripts/generate_policies.py --force   # 覆盖已存在的文件

确定性生成：同一份代码永远产出同样的文档。已经存在的文件默认保留
（data/policies/ 里原有的 8 份是手写的，质量更高，不会被覆盖）。
"""
from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY_DIR = ROOT / "data" / "policies"

# 品类 -> 参数（无理由天数 / 质量售后天数 / 大额门槛 / 特殊备注）
CATEGORY_RULES: dict[str, dict] = {
    "耳机": {"no_reason": 7, "quality": 15, "big": 1000, "note": "已拆封的耳机仍可申请质量问题售后,需提供故障描述"},
    "食品": {"no_reason": 0, "quality": 3, "big": None, "note": "生鲜/冷藏类需在签收后24小时内反馈变质问题"},
    "服饰": {"no_reason": 7, "quality": 15, "big": None, "note": "吊牌已剪除或已水洗的服饰不支持无理由退货"},
    "家居": {"no_reason": 7, "quality": 30, "big": 2000, "note": "床品、贴身家纺拆封后不支持无理由退货"},
    "美妆": {"no_reason": 7, "quality": 15, "big": None, "note": "已拆封的护肤/彩妆不支持无理由退货(过敏需提供凭证)"},
    "数码配件": {"no_reason": 7, "quality": 30, "big": 2000, "note": "已激活或绑定账号的数码配件不支持无理由退货"},
    "图书": {"no_reason": 7, "quality": 15, "big": None, "note": "已拆封的教辅、套装书不支持无理由退货"},
    "母婴": {"no_reason": 7, "quality": 30, "big": None, "note": "入口类、贴身类母婴商品拆封后不支持无理由退货"},
    "运动户外": {"no_reason": 7, "quality": 30, "big": 1500, "note": "有明显使用痕迹的装备不支持无理由退货"},
    "家电": {"no_reason": 7, "quality": 90, "big": 3000, "note": "大家电需保留原包装,拆装由品牌售后负责"},
    "珠宝": {"no_reason": 7, "quality": 30, "big": 5000, "note": "定制款珠宝不支持无理由退货"},
    "虚拟商品": {"no_reason": 0, "quality": 7, "big": None, "note": "已使用的充值、会员类虚拟商品不支持退款"},
}

ASPECTS = ("退货", "换货", "退款", "运费")


def _bullets(lines: list[str]) -> str:
    return "\n".join(f"- {line}" for line in lines)


def _doc(category: str, aspect: str, rule: dict) -> str:
    no_reason, quality, big, note = rule["no_reason"], rule["quality"], rule["big"], rule["note"]
    head = f"# {category}类商品{aspect}规则\n\n## 适用范围\n适用于平台自营及第三方商家的{category}类商品。\n"

    if aspect == "退货":
        rules = []
        if no_reason:
            rules.append(f"{category}类商品支持7天无理由退货(签收之日起{no_reason}天内)")
        else:
            rules.append(f"{category}类商品不支持7天无理由退货")
        rules.append(f"{category}类商品质量问题退货期限为{quality}天")
        rules.append("退货需保证商品、配件、包装与赠品齐全,不影响二次销售")
        if big:
            rules.append(f"单笔金额超过{big}元的退货属于大额退货,需人工审核")
        body = head + "\n## 退货规则\n" + _bullets(rules) + "\n\n## 例外\n" + _bullets([
            note, "超过上述时限的退货申请需转人工审核",
        ]) + "\n\n## 说明\n退货申请创建后请在7天内寄回,并回填退回运单号。\n"

    elif aspect == "换货":
        rules = [
            f"质量问题支持换货,期限与质量售后期限一致({quality}天)",
            "同款缺货时可更换同价位商品,或转为退款处理",
            "换货商品需与原订单商品型号、规格一致",
        ]
        if big:
            rules.append(f"单笔金额超过{big}元的换货需人工审核")
        body = head + "\n## 换货规则\n" + _bullets(rules) + "\n\n## 例外\n" + _bullets([
            note, "人为损坏、进液、私自拆修的商品不支持换货",
        ]) + "\n\n## 说明\n换货需先寄回原商品,验收通过后发出新品。\n"

    elif aspect == "退款":
        rules = [
            f"{category}类商品退款在退货商品签收后3个工作日内发起,按原支付渠道退回",
            "退款金额以实付金额为准,不包含已使用的优惠券与积分",
            f"{category}类商品的赠品与配件需一并退回,缺失时按价值扣减",
        ]
        if no_reason == 0:
            rules.append(f"{category}类商品未发货时可申请全额退款")
        if big:
            rules.append(f"单笔金额超过{big}元的{category}退款需人工审批")
        body = head + "\n## 退款规则\n" + _bullets(rules) + "\n\n## 例外\n" + _bullets([
            note, "已开票订单退款需先完成发票红冲",
        ]) + "\n\n## 说明\n到账时间以支付渠道为准,银行渠道通常为1~3个工作日。\n"

    else:  # 运费
        rules = [
            f"质量问题导致的{category}退换货,往返运费由平台承担",
            f"{category}类商品7天无理由退货的退回运费由买家承担",
            f"{category}类商品如属大件或需专业包装,退回运费按实际物流报价结算",
        ]
        if big:
            rules.append(f"单笔金额超过{big}元的{category}退货运费需人工核定")
        body = head + "\n## 运费规则\n" + _bullets(rules) + "\n\n## 例外\n" + _bullets([
            note, "使用非平台指定物流导致的运费及丢损,平台不承担责任",
        ]) + "\n\n## 说明\n运费补贴在退货验收通过后随退款一并结算。\n"

    return body


# 不分品类的通用规则文档
GENERAL_DOCS: list[tuple[str, str]] = [
    ("发票规则", """# 发票规则

## 开票
- 订单完成后可在订单详情申请电子发票,默认开具电子普票
- 增值税专用发票需提供开票资质,3个工作日内开具

## 换开与红冲
- 发票信息有误可在开票后30天内申请换开
- 已开票订单发生退货,须先完成发票红冲再退款

## 说明
发票金额以实付金额为准,不含优惠券抵扣部分。
"""),
    ("价格保护规则", """# 价格保护规则

## 价保范围
- 自营商品在签收后15天内降价,可申请价保退差
- 价保仅针对商品直降,不含优惠券、满减与秒杀

## 例外
- 使用促销、以旧换新、企业采购的订单不支持价保
- 赠品活动与限时秒杀商品不支持价保

## 说明
价保差额以实付金额与新价格之差为准,原路退回。
"""),
    ("以旧换新规则", """# 以旧换新规则

## 适用范围
- 支持以旧换新的品类:手机、平板、笔记本、智能手表、家电
- 旧机估价由第三方服务商检测后确认

## 流程
- 下单时选择以旧换新,先寄出旧机并完成检测
- 检测价与预估价不一致时可选择确认或退回旧机

## 说明
补贴金额直接抵扣新机订单,不单独退款。
"""),
    ("预售商品规则", """# 预售商品规则

## 定金与尾款
- 预售定金支付后,尾款需在约定时间内支付,逾期定金不退
- 尾款支付后按预售承诺时间发货

## 退款
- 未付尾款前可申请退还定金(视活动规则)
- 已付尾款按普通订单的退货退款规则处理

## 说明
预售商品的发货时效以商品页公示为准。
"""),
    ("跨境商品规则", """# 跨境商品规则

## 适用范围
- 适用于保税仓与海外直邮的跨境商品
- 跨境商品需提供订购人身份信息用于清关

## 退换
- 跨境商品因清关要求,无质量问题不支持7天无理由退货
- 质量问题可在签收后7天内申请退换,退回需符合海关要求

## 说明
跨境订单退款需扣除已产生的税费与运费。
"""),
    ("大件配送与安装规则", """# 大件配送与安装规则

## 配送
- 大件商品(家电、家具)支持预约配送,配送前1天电话联系
- 需确认楼道、门洞尺寸,不符合条件可拒收

## 安装
- 品牌安装服务由品牌售后预约,安装免费(材料费另计)
- 安装后发现质量问题,由品牌售后按三包处理

## 说明
大件退回需保留原包装,无包装可能产生额外费用。
"""),
    ("售后凭证要求", """# 售后凭证要求

## 质量问题的凭证
- 需提供故障描述,必要时提供照片或视频
- 涉及功能故障的,需配合品牌售后检测

## 无效凭证
- 无法复现且无凭证的质量主张可直接转人工处理

## 说明
凭证用于判断责任归属,不影响用户依法享有的售后权利。
"""),
    ("退货时效计算规则", """# 退货时效计算规则

## 起算时间
- 7天无理由退货自签收之日起计算,签收当日不计入
- 质量问题售后期限自签收之日起计算

## 时效判定
- 以系统记录的签收时间为准,用户自行签收时间不改变判定
- 超过时效的申请默认转人工审核,可特事特办

## 说明
时效规则与各品类专属规则冲突时,以品类专属规则为准。
"""),
    ("退货物流规范", """# 退货物流规范

## 寄回要求
- 优先使用平台推荐的退货物流,可享运费险
- 需在申请后7天内寄回,并回填运单号

## 验收
- 仓库验收通过后触发退款,验收不通过会退回商品并说明原因

## 说明
自行选择非推荐物流的,丢损风险由用户承担。
"""),
    ("换货与退款选择指引", """# 换货与退款选择指引

## 如何选择
- 希望保留商品但需更换的,选择换货
- 不希望继续交易的,选择退货退款

## 组合场景
- 换货过程中同款缺货,可转为退款
- 部分商品退、部分换的订单,需拆单处理

## 说明
换货与退货退款同一订单内可分别处理,互不影响。
"""),
]


def generate(policy_dir: Path = POLICY_DIR, force: bool = False) -> dict:
    policy_dir.mkdir(parents=True, exist_ok=True)
    written, skipped = [], []

    def _write(stem: str, content: str) -> None:
        path = policy_dir / f"{stem}.md"
        if path.exists() and not force:
            skipped.append(stem)
            return
        path.write_text(content, encoding="utf-8")
        written.append(stem)

    for category, rule in CATEGORY_RULES.items():
        for aspect in ASPECTS:
            _write(f"{category}类商品{aspect}规则", _doc(category, aspect, rule))

    for stem, content in GENERAL_DOCS:
        _write(stem, content)

    total = len(list(policy_dir.glob("*.md")))
    return {"written": written, "skipped": skipped, "total_docs": total}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="覆盖已存在的文件")
    args = parser.parse_args()
    stats = generate(force=args.force)
    print(f"知识库目录: {POLICY_DIR}")
    print(f"  新增/更新 {len(stats['written'])} 份, 保留 {len(stats['skipped'])} 份")
    print(f"  当前共 {stats['total_docs']} 份文档")


if __name__ == "__main__":
    main()
