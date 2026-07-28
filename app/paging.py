"""轻量分页对象。

当排序结果已提前缓存在内存（按 id 列表）时，用它替代 flask_sqlalchemy 的
Pagination，避免对带聚合子查询的重查询再次执行 COUNT + 排序。
接口仅暴露模板 `macros/pagination.html` 所需的属性/方法。
"""
from math import ceil


class IdListPagination:
    def __init__(self, items, page, per_page, total):
        self.items = items
        self.page = page
        self.per_page = per_page
        self.total = total
        self.pages = int(ceil(total / per_page)) if per_page else 0

    @property
    def has_prev(self):
        return self.page > 1

    @property
    def has_next(self):
        return self.page < self.pages

    @property
    def prev_num(self):
        return self.page - 1 if self.has_prev else None

    @property
    def next_num(self):
        return self.page + 1 if self.has_next else None

    def iter_pages(self, left_edge=2, left_current=2, right_current=5, right_edge=2):
        """模仿 flask_sqlalchemy 的 iter_pages：用 None 表示被省略的页码区间。"""
        last = self.pages
        if last <= 0:
            return
        left = self.page - left_current - 1
        right = self.page + right_current
        numbers = set()
        for i in range(1, min(left_edge + 1, last + 1)):
            numbers.add(i)
        for i in range(max(left, 1), min(right, last) + 1):
            numbers.add(i)
        for i in range(max(last - right_edge + 1, 1), last + 1):
            numbers.add(i)
        prev = 0
        for p in sorted(numbers):
            if prev and p - prev > 1:
                yield None
            yield p
            prev = p
