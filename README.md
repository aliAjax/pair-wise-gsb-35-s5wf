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
- `POST /api/epochs/{id}/points`：设置已知点或未知点近似坐标（复核启动后锁定）。
- `POST /api/epochs/{id}/observations`：录入 `distance`、`angle`、`height_difference` 观测；疑似重复值会返回 409。
- `PATCH /api/epochs/{id}/observations/{obs_id}`：编辑修正观测；复核启动后只有 `returned` 状态的观测可改。
- `DELETE /api/epochs/{id}/observations/{obs_id}`：删除观测；复核启动后只能删除退回记录。
- `POST /api/epochs/{id}/adjust`：迭代加权最小二乘平差；残差超过 3.5 倍单位权中误差的观测标记为 `outlier`。
- `POST /api/epochs/{id}/transition/submit|approve|publish`：审核发布状态机。
- `POST /api/epochs/{id}/observations/{obs_id}/confirm`：审核员逐条确认观测，确认后记录锁定。
- `POST /api/epochs/{id}/observations/{obs_id}/return`：审核员单条退回观测，必须填写 `reason`；整期回到草稿，其他已确认记录不动。
- `POST /api/epochs/{id}/results/{point}/confirm`：审核员确认单个点位精度结果。
- `GET /api/epochs/{id}/results`：查看点位坐标和精度（含每条结果的复核状态）。
- `GET /api/epochs/{id}/compare/{other_id}`：比较两期成果。
- `GET /api/epochs/{id}/audit`：查看操作审计。

## 逐条观测复核

期次提交后，审核员在复核页面逐条确认边长、角度和高差，每条观测的 `review_status` 为：

- `pending`：待复核。
- `confirmed`：确认通过并锁定；重新平差不会重置已确认观测。
- `returned`：退回，带 `review_reason`、`reviewed_by`、`reviewed_at`；编辑只能修改或删除退回记录。

单条退回时整期回到 `draft`，但已经确认的观测和精度结果保持锁定。编辑修正退回观测（或补录、删除）后必须重新平差：只有变动观测（端点命中 `dirty` 观测的点）的精度结果会重置为 `pending` 重审，其余已确认精度结果保留。重新提交后，变动记录与受影响精度结果重新送审，已确认数据不重来。

批准要求所有有效观测和全部点位精度结果都已 `confirmed`；身份不是审核员、审核员是创建/提交人、或跳过任何复核都不能批准和发布。整期退回接口已取消，发现问题只能单条退回。

## 业务约束

- 只有在 `draft` 状态可修改观测和重新平差；首次提交前点数据可改，复核启动后点数据锁定。提交后必须由非创建人、非提交人的 `reviewer` 逐条复核、批准后才能发布。
- 观测确认后锁定；退回只针对单条，退回后整期回草稿，编辑只能动被退回的记录（补录新观测允许），修正后必须重新平差才能再提交。
- 观测变动（修正/删除/补录）会标记 `dirty`；重新平差后只有变动观测端点的精度结果重新送审，其他已确认结果保留。
- 同类型、同端点、容差内的观测被视为重复；确实需要保留时可在请求体传 `allow_duplicate: true`。
- 法方程秩亏或坐标不可解时返回 422，不会写出平差结果。
- 每期结果独立保存，发布只改变期次状态，不删除历史观测。

## 测试

```bash
python -m unittest discover -s tests -v
```

测试覆盖完整示例平差、提交审核发布、重复观测冲突和越权操作。
