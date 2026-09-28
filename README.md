# 大地测量网平差

一个仅使用 Python 标准库实现的大地测量观测管理与平差服务。支持角度、边长、高程差、已知点和未知点近似坐标，使用加权最小二乘求解，并输出残差、单位权中误差和点位精度。

## 运行

需要 Python 3.11+。

```bash
python app.py --init
python app.py --port 8006
```

浏览器打开 <http://127.0.0.1:8006>。数据库默认写入 `geodetic.db`，可以通过 `--db` 或环境变量 `GEODETIC_DB` 修改。

`--init` 会初始化一个包含 A、B 已知点和 C、D 待求点的示例期次。重复执行不会重复添加。

## 主要 API

所有修改接口使用 `X-User`、`X-Role` 请求头区分身份，角色为 `editor`、`reviewer` 或 `viewer`。

- `POST /api/epochs`：创建期次。
- `POST /api/epochs/{id}/points`：设置已知点或未知点近似坐标。
- `POST /api/epochs/{id}/observations`：录入 `distance`、`angle`、`height_difference` 观测；疑似重复值会返回 409。
- `POST /api/epochs/{id}/adjust`：迭代加权最小二乘平差；残差超过 3.5 倍单位权中误差的观测标记为 `outlier`。
- `POST /api/epochs/{id}/transition/submit|approve|publish`：审核发布状态机。
- `POST /api/epochs/{id}/observations/{obs_id}/confirm|return`：审核员逐条确认或单条退回观测（`return` 需在请求体传 `reason`）。
- `POST /api/epochs/{id}/observations/{obs_id}/revise`：编辑修订被退回的观测值或权。
- `POST /api/epochs/{id}/results/{point}/confirm`：审核员逐点复核平差精度结果。
- `GET /api/epochs/{id}/results`：查看点位坐标和精度（含每点的复核状态）。
- `GET /api/epochs/{id}/compare/{other_id}`：比较两期成果。
- `GET /api/epochs/{id}/audit`：查看操作审计。

## 业务约束

- 只有在 `draft` 状态可修改点、观测和重新平差；提交后必须由非创建人、非提交人的 `reviewer` 批准才能发布。
- 提交前必须先完成平差；进入复核后审核员对边长、角度、高差**逐条确认**，每条观测带 `pending / confirmed / returned` 状态、退回原因、审核人和修订版本号。
- 发现某条观测有偏差时只能退回该条：期次回到 `draft`，该条带原因置为 `returned`，其余已确认记录保持锁定、无需重来。
- 编辑只能修订被退回的观测（修订后回到 `pending`、版本号加一），修正并重新平差后，只有该条变动记录与退回后新增的观测需要重新确认，精度结果也只有变动观测端点相关的点重新置为待复核，其他点沿用已确认结论。
- 批准要求全部观测逐条确认且所有待复核精度结果均已复核；身份不符、未平差或跳过复核都不能批准/发布。整期 `reject` 已移除，统一使用单条退回。
- 同类型、同端点、容差内的观测被视为重复；确实需要保留时可在请求体传 `allow_duplicate: true`。
- 法方程秩亏或坐标不可解时返回 422，不会写出平差结果。
- 每期结果独立保存，发布只改变期次状态，不删除历史观测。旧数据库启动时自动迁移，补齐逐条复核相关列。

## 测试

```bash
python -m unittest discover -s tests -v
```

测试覆盖完整示例平差、提交审核发布、重复观测冲突和越权操作。
