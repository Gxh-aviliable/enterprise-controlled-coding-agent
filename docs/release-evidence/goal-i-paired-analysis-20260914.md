# Single/Multi 配对诊断

冻结源码 `e7635280120cfb897b2c1b298b8fd0e33106b9b0b9591b0deb56ff09449d54aa`；18对任务、36次执行。

|模式|通过|token中位数|耗时中位数ms|重复路径读取|父任务剩余token中位数|
|---|---:|---:|---:|---:|---:|
|single|17/18|62027.5|16389.0|9|3937972.5|
|multi|9/18|124793.0|38561.0|60|3875207.0|

所有逐例原始轨迹、范围、子执行时间和父任务预算见同名JSON及配对index。

- Three paired repeats per case are exploratory, not general statistical proof.
- Raw trace supplies child scope, start/end, delivery and following parent model/write events; delivery does not prove semantic use.
- Repeated reads can be legitimate after changes; counts are not automatic waste.
- Local diagnostic tests/builds also ran on this host; latency is not a dedicated-host benchmark.
- All failures and original budgets retained; child permissions remain read-only.
