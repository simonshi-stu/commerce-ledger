"""本地共享 API Key 的两级权限，不是账号密码或多租户身份系统。"""
import secrets
from .service import BusinessError


class KeyPolicy:
    def __init__(self, manager_key, reader_key=""):
        if not manager_key or not manager_key.strip():
            raise RuntimeError("必须显式设置 COMMERCE_API_KEY，没有默认管理密钥")
        if reader_key and secrets.compare_digest(manager_key.encode(), reader_key.encode()):
            raise RuntimeError("管理密钥与只读密钥必须不同")
        if reader_key and not reader_key.strip():
            raise RuntimeError("只读密钥不能全部为空白")
        self.manager_key = manager_key.encode()
        self.reader_key = reader_key.encode()

    def authenticate(self, provided):
        candidate = (provided or "").encode()
        manager = secrets.compare_digest(candidate, self.manager_key)
        reader = bool(self.reader_key) and secrets.compare_digest(candidate, self.reader_key)
        if manager:
            return "manager"
        if reader:
            return "reader"
        raise BusinessError(401, "UNAUTHORIZED", "API Key 不正确")

    def authorize(self, provided, method):
        role = self.authenticate(provided)
        if method not in {"GET", "HEAD", "OPTIONS"} and role != "manager":
            raise BusinessError(403, "FORBIDDEN", "只读凭据不能修改业务数据")
        return role
