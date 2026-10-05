class OpenApiError(Exception):
    """OpenAPI 面错误；`params` 用于让文案模板（含 `{{name}}` 之类占位）填得满。

    docs/08 打包 BC：填不满时前端会按既有守卫回退后端中文原文，所以带插值的消息
    **必须**同时给 params，否则英文态拿不到等价信息。
    """

    def __init__(self, code: str, message: str, params: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.params = params


class UnsupportedSchema(OpenApiError):
    """解析期"这一段 schema 我们支持不了"。

    docs/08 打包 BC：以前 6 种失败共用 `OPENAPI_INVALID_DOCUMENT` 一个码、靠 `reason`
    区分——按码出模板会把「请求体仅支持 application/json」和「参数缺少 name/in」压成同一句。
    现在**调用方必须给具体码**，`code` 是必填位置参数，没有默认值兜底（给默认值＝
    下一个人又会写出一条多话的码）。
    """

    def __init__(self, code: str, reason: str, params: dict[str, object] | None = None) -> None:
        super().__init__(code, reason, params)
        self.reason = reason
