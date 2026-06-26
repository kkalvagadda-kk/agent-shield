rule SQLInjection {
    meta:
        description = "SQL injection patterns"
        severity = "critical"
    strings:
        $a = /(?i)(union\s+select|drop\s+table|insert\s+into|delete\s+from|update\s+.*\s+set)/
        $b = /(?i)('\s*or\s+'1'\s*=\s*'1|--\s*$|;\s*drop)/
    condition:
        any of them
}

rule XSSPayload {
    meta:
        description = "Cross-site scripting (XSS) payloads"
        severity = "high"
    strings:
        $a = "<script>" nocase
        $b = "</script>" nocase
        $c = "javascript:" nocase
        $d = "onerror=" nocase
        $e = "onload=" nocase
        $f = "<img" nocase
        $g = "alert(" nocase
    condition:
        any of them
}

rule TemplateInjection {
    meta:
        description = "Jinja2 and generic template injection patterns"
        severity = "critical"
    strings:
        $a = "{{"
        $b = "}}"
        $c = "{%"
        $d = "%}"
    condition:
        any of them
}

rule PythonCodeInjection {
    meta:
        description = "Python code injection and dangerous builtins"
        severity = "critical"
    strings:
        $a = "__import__" nocase
        $b = "eval(" nocase
        $c = "exec(" nocase
        $d = "os.system" nocase
        $e = "subprocess." nocase
        $f = "__builtins__" nocase
        $g = "compile(" nocase
    condition:
        any of them
}

rule SystemPromptExtraction {
    meta:
        description = "Attempts to extract or override system prompt instructions"
        severity = "high"
    strings:
        $a = "ignore previous instructions" nocase
        $b = "ignore all previous" nocase
        $c = "you are now" nocase
        $d = "disregard your" nocase
        $e = "forget your instructions" nocase
        $f = "your new instructions are" nocase
        $g = "act as if you have no restrictions" nocase
        $h = "pretend you are" nocase
    condition:
        any of them
}
