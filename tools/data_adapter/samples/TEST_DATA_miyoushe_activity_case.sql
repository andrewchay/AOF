-- TEST_DATA: 米游社活跃用户指标案例 SQL（仅用于流程验证，非生产口径）

-- TEST_DATA: 米游社 DAU（移动端）
SELECT
    logdate,
    COUNT(DISTINCT account_id) AS dau_app
FROM dws_community.dws_community_user_login_snapshot
WHERE logdate = '2026-03-01'
  AND logregion = 'pro'
  AND platform = 'APP_ALL'
  AND game_id = -1
  AND account_id > 0
  AND substr(daily_active_bitmap, 1, 1) = '1'
GROUP BY logdate;

-- TEST_DATA: 2026年1月米游社去重活跃用户数（MAU）
SELECT
    COUNT(DISTINCT account_id) AS mau_app
FROM dws_community.dws_community_user_login_snapshot
WHERE logdate BETWEEN '2026-01-01' AND '2026-01-31'
  AND logregion = 'pro'
  AND platform = 'APP_ALL'
  AND game_id = -1
  AND account_id > 0
  AND substr(daily_active_bitmap, 1, 1) = '1';
