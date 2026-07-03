# qbot —— 诚实、可回测、默认安全的量化交易系统

> 覆盖 **OKX 全部加密货币** 与 **全 A股**，带可视化面板、回测引擎、风控熔断。
> 由一个"20 年交易 + 30 年编程"的视角设计。

---

## 🚨 先读这段：一个老交易员的实话

你的原话是"回测很低、年化很高、胜率 60-70%"。我必须诚实地告诉你：

- **没有人能保证这个。** 保证的不是骗子就是没亏够钱。
- **胜率高 ≠ 赚钱。** 胜率 90% 也能被一次不止损打爆。真正决定生死的是
  **最大回撤**和**盈亏比**。
- **所以这套系统的第一目标不是"高年化"，而是"不爆仓、可复现、不自欺"。**

我把三样最容易造假的东西全部做进了引擎，宁可回测数字难看，也不给你一条
"实盘一碰就碎"的假曲线：

| 常见回测骗局 | 本系统的处理 |
|---|---|
| 用当根K线信息交易（未来函数） | 信号 `shift(1)`，**今天信号明天成交** |
| 忽略手续费 | 每次调仓扣 `fee_rate`（默认万5） |
| 忽略滑点 | 买贵卖便宜，扣 `slippage`（默认万5） |
| 只报好看的年化 | 同时报**最大回撤/夏普/卡玛/盈亏比** |

**默认全程模拟盘。动真钱要你亲手改开关，且必须先在模拟盘跑稳。**

---

## 快速开始（3 步，无需联网即可看到效果）

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 跑一次回测（合成数据，离线可用）
python run_backtest.py --market synthetic --strategy rsi_reversion

# 3. 打开可视化面板
python run_dashboard.py
#    浏览器访问 http://127.0.0.1:8000
```

---

## 目录结构

```
qbot/
  data/        数据层：crypto(OKX/ccxt) · ashare(akshare) · synthetic(离线) · loader(统一入口)
  strategies/  策略框架：ma_cross 双均线 · rsi_reversion 均值回归 · donchian 通道突破
  backtest/    回测引擎(防未来函数+费用+滑点) + 绩效指标
  risk/        风控：仓位上限 · 单笔止损 · 组合回撤熔断 · 按风险定仓位
  broker/      下单通道：paper 模拟盘(默认) · okx 实盘/模拟盘
  engine/      24h 实盘引擎(策略→风控→下单)
  web/         FastAPI 可视化面板
run_backtest.py  回测 CLI
run_scan.py      信号扫描器（A股"数据+信号"路线核心）
run_live.py      实盘/模拟盘引擎
run_dashboard.py 启动面板
```

---

## 三个核心用法

### 1) 回测：验证一个策略在历史上表现如何
```bash
python run_backtest.py --market crypto --symbol BTC/USDT --timeframe 1d --strategy donchian --ppy 365
python run_backtest.py --market ashare --symbol 600519 --strategy ma_cross
```
输出：总收益、年化、最大回撤、夏普、索提诺、卡玛、胜率、盈亏比。
**看回测先看最大回撤和卡玛，别只盯年化。**

### 2) 扫描信号：让系统告诉你当前该买/该卖（A股推荐用法）
```bash
python run_scan.py --market ashare --symbols 600519 000001 300750 --strategy ma_cross
python run_scan.py --market crypto --symbols BTC/USDT ETH/USDT SOL/USDT --strategy donchian
```
输出一张清单：🟢买入 / ⚪空仓 / 🔴做空。**此工具不自动下单**，你据此手动/半自动操作。

### 3) 面板：可视化回测资金曲线、回撤、持仓
```bash
python run_dashboard.py   # 然后浏览器打开 http://127.0.0.1:8000
```

---

## A股怎么办？（你已选择"数据+信号"路线）

A股没有面向散户的官方开放下单 API。现实选项：

| 方案 | 说明 | 适合 |
|---|---|---|
| **数据+信号（本项目当前路线）** | akshare 免费拿全 A股数据 → 回测/选股/出信号 → 你手动下单 | **新手起步，0 门槛** |
| QMT（迅投）/ Ptrade | 在支持的券商（国金/华鑫等）开户并开通后可全自动 | 策略稳定后升级 |
| easytrader | 模拟点击券商客户端，脆弱易失效 | 不推荐认真用 |

将来要接 QMT 自动下单：`qbot/broker/` 已抽象出统一 `Broker` 接口，
照着 `okx.py` 再写一个 `qmt.py` 即可，上层策略/风控代码一行不用改。

---

## 加密（OKX）实盘怎么开？（你已选择"先模拟、再小额实盘"）

### 第一步：OKX 模拟盘（不花一分钱，强烈建议先跑几周）
1. OKX → 交易 → **模拟交易** → 生成**模拟盘** API Key（含 passphrase）。
2. 复制 `.env.example` 为 `.env`，填入 key，保持 `OKX_DEMO=1`。
3. 跑：
   ```bash
   python run_live.py --broker okx --market crypto --symbol BTC/USDT --strategy donchian --once
   ```
   `--once` 先只跑一个周期看日志；确认无误后去掉它就是 24h 循环。

### 第二步：小额实盘（务必看懂下面每一道风控再动手）
1. 在 `.env` 设 `OKX_DEMO=0`，换成真实 API Key。
2. 启动时**必须**显式加 `--i-understand-the-risk`，否则程序会自我保护中止。
3. 用**很小**的金额（比如几百 U）先验证真实成交和熔断。

### 你上真钱前必须理解的 5 道风控（在 `qbot/risk/manager.py`）
| 参数 | 默认 | 含义 |
|---|---|---|
| `max_position_per_symbol` | 20% | 单个标的最多占总资金两成，不押注单一品种 |
| `stop_loss_pct` | 8% | 单笔亏到 8% 就该止损 |
| `risk_per_trade` | 1% | 每笔交易最多亏总资金的 1%（据此反推仓位大小） |
| `max_portfolio_drawdown` | 20% | 组合整体回撤 20% **自动熔断**，清仓停手 |
| 手续费+滑点 | 各万5 | 回测已计入，别让高频把利润磨光 |

---

## 24 小时运行

`run_live.py` 是一个 `while` 轮询循环。生产环境建议用进程守护（Linux `systemd`/
`supervisor`，Windows 任务计划）拉起，崩溃自动重启。单周期异常已被 `try/except`
兜住，不会因一次网络抖动弄挂整个引擎。

---

## 常见问题

- **拉不到行情/ 没网？** loader 会自动回退到合成数据并打印告警，流程不中断。
- **想加自己的策略？** 在 `qbot/strategies/` 照着 `ma_cross.py` 写一个类，
  实现 `generate_positions(df)` 返回目标仓位序列，再在 `__init__.py` 注册即可。
  回测、扫描、实盘、面板会自动认得它。
- **想跑测试？** `python -m pytest tests/` —— 守护"防未来函数/风控"不被改坏。

---

## ⚠️ 风险声明

本项目是**教学与研究用的工具**，不是投资建议，不承诺任何收益。量化交易有亏损全部
本金的风险。任何策略在历史上的表现都不代表未来。请先用模拟盘充分验证，实盘从小额
开始，并对自己的每一笔交易负责。
