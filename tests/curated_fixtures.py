"""明确虚构的工作簿，使用与负责人资料相同的列结构。"""

from pathlib import Path

from openpyxl import Workbook

from backend.app.curated_workbook import HEADERS

COMPANY_KEY = "C001"
COMPANY_NAME = "示例山海设备有限公司"
CREDIT_CODE = "91310000999999999J"


def workbook_file(path: Path, *, change=None) -> Path:
    data = {
        "公司总表": [
            {
                "公司ID": COMPANY_KEY,
                "项目简称": "示例山海",
                "工商全称": COMPANY_NAME,
                "统一社会信用代码": CREDIT_CODE,
                "地区（资料口径）": "示例地区",
                "主体来源1": "负责人核对资料",
                "主体公开链接1": "",
            }
        ],
        "事件明细": [
            {
                "事件ID": "E001",
                "公司ID": COMPANY_KEY,
                "事件/披露日期": "2026-06-01",
                "事件类别": "融资",
                "重要事件": "示例品牌完成A轮融资",
                "事件摘要": "示例品牌完成近一亿元融资，示例机构参与投资。",
                "证据等级": "B｜署名媒体报道",
                "内容支持状态": "媒体报道支持",
                "工商全称": COMPANY_NAME,
                "统一社会信用代码": CREDIT_CODE,
                "日期精度": "日",
                "日期口径": "公开报道日",
                "实际发生日期": "未单独确认",
                "披露日期": "2026-06-01",
                "主体归属口径": "品牌融资；未确认法人增资",
                "交易/进程组": "G001",
                "信息来源1": "示例媒体一",
                "公开链接1": "https://example.invalid/news/one",
                "信息来源2": "示例媒体二",
                "公开链接2": "https://example.invalid/news/two",
                "核验说明": "仅虚构测试",
                "资料截止日期": "2026-09-01",
            },
            {
                "事件ID": "E002",
                "公司ID": COMPANY_KEY,
                "事件/披露日期": "2024-04",
                "事件类别": "融资",
                "重要事件": "示例品牌完成天使轮融资",
                "事件摘要": "示例品牌获得天使轮投资，金额未披露。",
                "证据等级": "A2｜公司/投资方披露",
                "内容支持状态": "已核对当事方披露",
                "工商全称": COMPANY_NAME,
                "统一社会信用代码": CREDIT_CODE,
                "日期精度": "月",
                "日期口径": "公司回溯月份",
                "实际发生日期": "未单独确认",
                "披露日期": "未确认",
                "主体归属口径": "品牌融资；未确认法人增资",
                "交易/进程组": "G002",
                "信息来源1": "示例公司",
                "公开链接1": "https://example.invalid/history",
                "资料截止日期": "2026-09-01",
            },
        ],
        "证据来源": [
            {
                "来源ID": "S001",
                "发布者/载体": "示例媒体一",
                "标题": "虚构融资报道",
                "公开URL": "https://example.invalid/news/one",
                "可读取范围": "人工整理",
                "证据定位提示": "段落一",
                "来源使用限制": "只存最小片段",
            }
        ],
        "待核事项": [
            {
                "线索ID": "L001",
                "公司ID": COMPANY_KEY,
                "资料日期": "2026-07",
                "待核主题": "待核的股东变化",
                "目前信息": "线索尚无进一步证据。",
                "处理口径": "待核，不作为事实",
                "公开链接/定位": "示例人工定位",
                "补证要求": "后续按需复核",
            }
        ],
    }
    if change:
        change(data)
    workbook = Workbook()
    workbook.remove(workbook.active)
    for name, headers in HEADERS.items():
        sheet = workbook.create_sheet(name)
        sheet.append(["明确虚构的 E4.1 测试数据"])
        for _ in range(3):
            sheet.append([])
        sheet.append(headers)
        for record in data[name]:
            sheet.append([record.get(column) for column in headers])
    workbook.save(path)
    workbook.close()
    return path
