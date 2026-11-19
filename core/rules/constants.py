"""模拟常数。全部集中在这里，方便校准与调参。

标注 [待校准] 的值来自 tools/refcalc.py 的初始估计；
跑过 tools/bench_mysql.py 后由 content/calibration.json 覆盖。
"""

from __future__ import annotations

# ---- 时间
DAYS_PER_WEEK = 7
WEEKS_PER_MONTH = 4
DAYS_PER_MONTH = DAYS_PER_WEEK * WEEKS_PER_MONTH

# ---- 页与存储
PAGE_BYTES = 16 * 1024

# ---- 查询成本（[待校准]）
C_ROW_US = 0.06          # 扫一行的 CPU（微秒）
C_PAGE_US = 0.20         # 访问一页的 CPU（微秒）
C_IO_RAND_MS = 0.11      # 随机读一页
C_IO_SEQ_MS = 0.125      # 顺序读一页（受 128MB/s 吞吐限制）
C_PARSE_MS = 0.12        # 解析 + 网络 + 应用固定开销

# ---- Buffer pool
SKEW = 0.75              # 热点集中系数
INDEX_HIT = 0.98         # 小索引常驻内存的命中率
HIT_MIN, HIT_MAX = 0.30, 0.99

# ---- 磁盘
DEFAULT_IOPS = 3000
DISK_WAIT_CAP_MS = 40.0  # 饱和时的单次 IO 等待上限

# ---- 流量
DAU_RATIO = 0.1333
REQ_PER_DAU_DAY = 2.4 * 18      # 43.2
PEAK_FACTOR = 6.5

# ---- 应用层
API_OVERHEAD_MS = 15.0
API_ARRIVAL_SHARE = 0.55        # 高峰窗口里打到应用层的比例

# ---- 写入
C_WRITE_BASE_MS = 1.10
C_INDEX_INSERT_MS = 0.18

# ---- 增长
G_ORGANIC_MONTH = 0.04
CHURN_BASE_MONTH = 0.012
CHURN_MAX_MONTH = 0.12          # 单月流失上限：一次事故不能把数据打回原点

# ---- 转化
ORDER_RATE_PER_DAU = 0.125
AOV_YUAN = 85
TAKE_RATE = 0.06

# ---- 其他查询桶：每次请求约 2 页随机读
OTHER_SHARE = 0.05
OTHER_IO_OPS = 2.0

# ---- 建索引
BUILD_ROWS_PER_SEC = 30_000
