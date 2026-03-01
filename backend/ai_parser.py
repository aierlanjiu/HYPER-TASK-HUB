"""
AI 结构化输出解析器
从审计专员的自然语言回复中提取 [TARGET] [TASK] [PRIORITY] [ACTION] 等机器标签
"""
import re
from typing import Optional

class DispatchDirective:
    """审计专员解析后的调度指令"""
    def __init__(self):
        self.target: Optional[str] = None      # 目标特工 ID
        self.task: Optional[str] = None         # 任务标题
        self.priority: str = "MEDIUM"           # 优先级
        self.action: str = "EXECUTE"            # 动作类型
        self.agent: Optional[str] = None        # OpenClaw 子会话 ID
        self.task_id: Optional[str] = None      # 目标操作的任务 ID
        self.context: Optional[str] = None      # 附加上下文
        self.raw_response: str = ""             # AI 原始回复（含自然语言部分）
        self.briefing: str = ""                 # 自然语言简报（去掉标签后）
    
    @property
    def is_valid(self) -> bool:
        """至少要有 target 和 task 才算有效"""
        return bool(self.target and self.task and self.target != 'none')
    
    @property
    def is_escalation(self) -> bool:
        return self.action == 'ESCALATE'
    
    def to_dict(self) -> dict:
        return {
            "target": self.target,
            "task": self.task,
            "priority": self.priority,
            "action": self.action,
            "agent": self.agent,
            "task_id": self.task_id,
            "context": self.context,
            "briefing": self.briefing,
        }
    
    def __repr__(self):
        return f"<Directive target={self.target} task={self.task!r} priority={self.priority} action={self.action}>"


# 标签名 → 正则模式
TAG_PATTERNS = {
    'target':   r'\[TARGET:\s*(.+?)\]',
    'task':     r'\[TASK:\s*(.+?)\]',
    'priority': r'\[PRIORITY:\s*(.+?)\]',
    'action':   r'\[ACTION:\s*(.+?)\]',
    'agent':    r'\[AGENT:\s*(.+?)\]',
    'task_id':  r'\[TASK_ID:\s*(.+?)\]',
    'context':  r'\[CONTEXT:\s*(.+?)\]',
}

# 合法值校验
VALID_TARGETS = {'openclaw', 'deepseek-nas', 'gemini-bot', 'none'}
VALID_PRIORITIES = {'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'}
VALID_ACTIONS = {'EXECUTE', 'QUERY', 'REVIEW', 'ESCALATE', 'COMPLETE', 'CANCEL'}


def parse_ai_response(ai_response: str) -> DispatchDirective:
    """
    从审计专员的 AI 回复中解析结构化调度指令。
    
    示例输入:
        "这是工程任务，交给 OpenClaw。\n[TARGET: openclaw]\n[TASK: 重构登录模块]\n[PRIORITY: HIGH]\n[ACTION: EXECUTE]"
    
    返回:
        DispatchDirective 对象
    """
    directive = DispatchDirective()
    directive.raw_response = ai_response
    
    for key, pattern in TAG_PATTERNS.items():
        match = re.search(pattern, ai_response, re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            setattr(directive, key, value)
    
    # 标准化
    if directive.target:
        directive.target = directive.target.lower().strip()
    if directive.priority:
        directive.priority = directive.priority.upper().strip()
        if directive.priority not in VALID_PRIORITIES:
            directive.priority = 'MEDIUM'
    if directive.action:
        directive.action = directive.action.upper().strip()
        if directive.action not in VALID_ACTIONS:
            directive.action = 'EXECUTE'
    
    # 提取自然语言简报（去掉所有标签行）
    lines = ai_response.split('\n')
    briefing_lines = []
    for line in lines:
        if not re.match(r'^\s*\[(?:TARGET|TASK|PRIORITY|ACTION|AGENT|TASK_ID|CONTEXT):', line, re.IGNORECASE):
            stripped = line.strip()
            if stripped:
                briefing_lines.append(stripped)
    directive.briefing = '\n'.join(briefing_lines).strip()
    
    return directive


if __name__ == '__main__':
    # 测试用例
    test = """这是一个前端工程优化任务，涉及代码修改，我安排 OpenClaw 来处理。

[TARGET: openclaw]
[TASK: 优化 web-gallery 图片懒加载性能]
[PRIORITY: HIGH]
[ACTION: EXECUTE]"""
    
    d = parse_ai_response(test)
    print(d)
    print(f"Valid: {d.is_valid}")
    print(f"Briefing: {d.briefing}")
    print(f"Dict: {d.to_dict()}")
