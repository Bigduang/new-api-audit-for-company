from __future__ import annotations

from dataclasses import dataclass

from .schemas import ClassificationResult


WORK_KEYWORDS: dict[str, list[str]] = {
    "编码实现": [
        "实现", "编写", "写一个", "创建", "添加", "新增", "生成", "开发", "修改", "改成", "改为", "调整", "更新", "删除",
        "def", "function", "class", "const", "import", "async", "await", "component", "props", "state", "hook", "api", "endpoint",
        "route", "middleware", "controller", "service", "页面", "组件", "接口", "表单", "表格", "弹窗", "按钮", "导航", "菜单",
        "css", "scss", "tailwind", "模型", "model", "查询", "query", "orm", "事务", "transaction", "缓存", "cache", "token",
        "php", "laravel", "python", "django", "flask", "fastapi", "javascript", "typescript", "java", "spring", "go", "golang",
        "前端", "后端", "全栈", "sdk", "脚本", "script", "命令行", "cli",
    ],
    "调试修复": [
        "报错", "错误", "异常", "bug", "fix", "修复", "解决", "排查", "检查", "原因", "为什么", "不行", "有问题", "没生效",
        "500", "404", "stack", "trace", "exception", "error", "debug", "调试", "测试", "test", "单元测试", "集成测试",
        "e2e", "coverage", "assert", "mock", "日志", "log", "warning", "崩溃", "crash", "oom", "死锁", "deadlock",
    ],
    "架构设计": [
        "方案", "架构", "设计", "计划", "规划", "选型", "技术栈", "重构", "拆分", "抽象", "模式", "pattern", "设计模式",
        "微服务", "单体", "分布式", "集群", "负载均衡", "高可用", "扩展", "解耦", "数据库设计", "表结构", "索引",
        "消息队列", "kafka", "rabbitmq", "redis", "requirement", "spec", "prd",
    ],
    "配置运维": [
        "配置", "部署", "安装", "升级", "迁移", "备份", "恢复", "环境", "nginx", "docker", "npm", "pip", "composer", "git",
        "commit", "服务器", "域名", "证书", "ssl", "端口", "linux", "ubuntu", "shell", "bash", "cron", "systemd",
        "ci", "cd", "github", "actions", "gitlab", "docker-compose", "k8s", "terraform", "安全", "security", "监控",
    ],
    "文档编写": [
        "文档", "注释", "说明", "readme", "changelog", "记录", "写文档", "整理", "总结", "接口文档", "api文档",
        "swagger", "openapi", "需求文档", "技术文档", "wiki", "教程", "指南",
    ],
    "代码审查": [
        "review", "审查", "检查代码", "这段代码", "优化", "性能", "改进", "建议", "best", "practice", "规范",
        "代码风格", "lint", "eslint", "prettier", "refactor",
    ],
    "数据分析": [
        "sql", "select", "统计", "报表", "分析", "指标", "看板", "图表", "数据", "dataset", "excel", "csv", " BI ",
        "同比", "环比", "转化率", "漏斗",
    ],
}

NON_WORK_KEYWORDS = [
    "hello", "hi", "how are you", "joke", "story", "poem", "game", "movie", "music", "weather", "recipe", "travel",
    "你是谁", "你好", "讲个笑话", "故事", "天气", "新闻", "菜谱", "旅游", "推荐", "好吃", "好玩", "电影", "聊天", "闲聊",
    "写诗", "写小说", "周末", "放假", "出去玩", "谈心", "心理", "感情", "恋爱", "婚姻", "家庭", "孩子", "宠物", "购物",
    "淘宝", "京东", "拼多多", "外卖", "点餐", "吃什么", "减肥", "健身", "运动", "跑步", "游泳", "篮球", "足球",
    "王者", "原神", "lol", "steam", "switch", "ps5", "xbox", "追剧", "综艺", "八卦", "明星", "偶像", "星座", "算命",
    "塔罗", "占卜", "股票", "理财", "投资", "买房", "买车", "装修",
]


@dataclass(frozen=True)
class RuleScore:
    category: str
    score: int
    evidence: list[str]


def _score_keywords(text: str, keywords: list[str]) -> tuple[int, list[str]]:
    lower = text.lower()
    evidence = [kw for kw in keywords if kw.lower() in lower]
    return len(evidence), evidence[:8]


def classify_by_rules(text: str) -> ClassificationResult:
    compact = " ".join(text.split())
    if not compact:
        return ClassificationResult(
            category="其他",
            work_verdict="uncertain",
            confidence=0.2,
            reason="请求文本为空或未采集",
            evidence=[],
            needs_llm=True,
        )

    scores = []
    for category, keywords in WORK_KEYWORDS.items():
        score, evidence = _score_keywords(compact, keywords)
        scores.append(RuleScore(category=category, score=score, evidence=evidence))
    top = max(scores, key=lambda item: item.score)
    non_work_score, non_work_evidence = _score_keywords(compact, NON_WORK_KEYWORDS)
    work_score_total = sum(item.score for item in scores)

    if non_work_score > 0 and work_score_total < 2:
        confidence = min(0.95, 0.55 + non_work_score * 0.12)
        return ClassificationResult(
            category="疑似非工作",
            work_verdict="non_work",
            confidence=confidence,
            reason="命中非工作关键词，且缺少明显开发/工作关键词",
            evidence=non_work_evidence,
            needs_llm=True,
        )

    if top.score >= 3:
        confidence = min(0.95, 0.55 + top.score * 0.08)
        return ClassificationResult(
            category=top.category,
            work_verdict="work",
            confidence=confidence,
            reason=f"命中 {top.category} 相关关键词",
            evidence=top.evidence,
            needs_llm=False,
        )

    if top.score == 2:
        return ClassificationResult(
            category=top.category,
            work_verdict="work",
            confidence=0.66,
            reason=f"弱命中 {top.category} 相关关键词",
            evidence=top.evidence,
            needs_llm=True,
        )

    return ClassificationResult(
        category="其他",
        work_verdict="uncertain",
        confidence=0.35,
        reason="规则无法稳定判断是否工作用途",
        evidence=top.evidence + non_work_evidence,
        needs_llm=True,
    )

