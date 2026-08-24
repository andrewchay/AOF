# 企业本体治理工作台运行手册

## 安全边界

工作台不生成、保存或接触身份签名密钥。企业身份网关使用 `AOF_SEMANTIC_IDENTITY_KEY_ID` 与
`AOF_SEMANTIC_IDENTITY_SECRET` 签发短时 `x-aof-principal-*` envelope，浏览器只暂存 envelope。服务端验证
签名、时效、tenant 和 roles，并忽略请求正文中的 actor 声明。不得把 secret 注入 Vite 或静态文件；生产代理
应直接注入 headers，并禁止外部请求覆盖。租户状态位于 `data/ontology_governance/{tenant_id}`。

## 角色与职责分离

- `editor/owner` 创建和编辑；`validator` 运行门禁；`risk-owner` 记录豁免；
- `reviewer` 请求修改或审批；`publisher` 发布；`viewer` 查看证据。

创建者不能审批自己的草稿，审批者不能发布自己批准的版本；管理员同样受职责分离约束。

## 发布前验收

1. 在 `web/` 运行 `npm run build`。
2. 运行 `.venv/bin/python -m pytest tests/test_ontology_governance.py -q`。
3. 运行 `.venv/bin/python -m pytest -q`。
4. 用不同 subject 的 editor、validator、reviewer、publisher 完成真实 canary。
5. 确认工作台 evidence integrity 与 Decision ledger 均为 valid。

签名错误返回 401，未配置 verifier 返回 503，职责分离冲突返回 409。发布目录和决策账本必须进入不可变备份及
恢复演练。
