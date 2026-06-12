import json
import os

import redis

from config import ACCOUNTS_FILE, REDIS_KEY, REDIS_POOL, TASK_STATE_FILE

VIP_FURTHER_GET_TIME_KEY_TPL = "netease:music:user:{uid}:vip:furtherVipGetTime"


def _vip_key(user_uid) -> str:
    return VIP_FURTHER_GET_TIME_KEY_TPL.format(uid=str(user_uid))


class RedisStorage:
    name = "redis"

    def __init__(self, logger):
        self.logger = logger
        self.redis = redis.Redis(connection_pool=REDIS_POOL) if REDIS_POOL else None
        if not self.redis:
            raise RuntimeError("Redis连接池未初始化")
        self.redis.ping()

    def get_all_users_credentials(self):
        users = self.redis.hgetall('netease:music:task')
        user_list = []
        for task_key, info_str in users.items():
            try:
                info = json.loads(info_str)
                if all(key in info for key in ['phone', 'password']):
                    user_list.append({
                        'task_key': task_key,
                        'uid': info.get('uid', task_key),
                        'phone': info.get('phone'),
                        'password': info.get('password')
                    })
                else:
                    self.logger.warning(f"用户数据不完整，缺少必要字段: {task_key}")
            except json.JSONDecodeError:
                self.logger.error(f"解析用户数据失败: {task_key}")
            except Exception as e:
                self.logger.error(f"处理用户数据时发生异常: {e}")
        return user_list

    def update_user_uid(self, task_key, uid):
        if not task_key:
            return
        try:
            user_info_str = self.redis.hget('netease:music:task', task_key)
            if user_info_str:
                user_info = json.loads(user_info_str)
                if str(user_info.get('uid')) != str(uid):
                    user_info['uid'] = uid
                    self.redis.hset('netease:music:task', task_key, json.dumps(user_info))
                    self.logger.info(f"绑定真实 UID: {uid}")
        except Exception as e:
            self.logger.error(f"回写 UID 失败: {e}")

    def get_cookie(self, uid):
        return self.redis.get(f'netease:music:user:{uid}:cookie')

    def delete_session(self, uid):
        self.redis.delete(f'netease:music:user:{uid}:cookie')
        self.redis.delete(f'netease:music:user:{uid}:userdata')

    def save_session(self, uid, cookie_str, user_data):
        if not cookie_str:
            return False
        self.redis.set(f'netease:music:user:{uid}:cookie', cookie_str, ex=86400 * 30)
        self.redis.set(f'netease:music:user:{uid}:userdata', json.dumps(user_data), ex=86400 * 30)
        return True

    def update_cookie(self, uid, cookie_str):
        if not cookie_str:
            return False
        self.redis.set(f'netease:music:user:{uid}:cookie', cookie_str, ex=86400 * 30)
        return True

    def get_vip_further_get_time_ms(self, user_uid):
        value = self.redis.get(_vip_key(user_uid))
        if value is None:
            return None
        value = str(value).strip()
        if not value:
            return None
        if value.isdigit():
            return int(value)
        obj = json.loads(value)
        if isinstance(obj, (int, float)):
            return int(obj)
        if isinstance(obj, str) and obj.isdigit():
            return int(obj)
        return None

    def set_vip_further_get_time_ms(self, user_uid, ms):
        self.redis.set(_vip_key(user_uid), str(int(ms)))

    def load_send_records(self):
        data = self.redis.get(REDIS_KEY)
        if data:
            return json.loads(data)
        return {}

    def save_send_records(self, data):
        self.redis.set(REDIS_KEY, json.dumps(data, ensure_ascii=False))
        return True


class LocalJsonStorage:
    name = "local-json"

    def __init__(self, logger, accounts_file=ACCOUNTS_FILE, state_file=TASK_STATE_FILE):
        self.logger = logger
        self.accounts_file = accounts_file
        self.state_file = state_file

    def _load_accounts_data(self):
        if not os.path.exists(self.accounts_file):
            self.logger.warning(f"账号配置文件不存在：{self.accounts_file}")
            return []
        try:
            with open(self.accounts_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            self.logger.error(f"账号配置文件不是有效 JSON：{e}")
            return []
        except Exception as e:
            self.logger.error(f"读取账号配置文件失败：{e}")
            return []

        if isinstance(data, dict):
            accounts = data.get('accounts', [])
        elif isinstance(data, list):
            accounts = data
        else:
            self.logger.error("账号配置格式错误，应为数组或包含 accounts 数组的对象")
            return []

        if not isinstance(accounts, list):
            self.logger.error("账号配置中的 accounts 必须是数组")
            return []
        return accounts

    def _default_state(self):
        return {"send_records": {}, "vip_further_get_time": {}}

    def _load_state(self):
        if not os.path.exists(self.state_file):
            return self._default_state()
        try:
            with open(self.state_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return self._default_state()
            data.setdefault("send_records", {})
            data.setdefault("vip_further_get_time", {})
            return data
        except json.JSONDecodeError:
            self.logger.error(f"本地状态文件不是有效 JSON：{self.state_file}")
        except Exception as e:
            self.logger.error(f"读取本地状态文件失败: {e}")
        return self._default_state()

    def _save_state(self, data):
        state_dir = os.path.dirname(self.state_file)
        if state_dir:
            os.makedirs(state_dir, exist_ok=True)
        with open(self.state_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True

    def get_all_users_credentials(self):
        user_list = []
        for index, info in enumerate(self._load_accounts_data(), start=1):
            if not isinstance(info, dict):
                self.logger.warning(f"第 {index} 个账号配置不是对象，已跳过")
                continue
            if info.get('enabled') is False:
                continue
            if all(key in info for key in ['phone', 'password']):
                phone = info.get('phone')
                user_list.append({
                    'task_key': str(info.get('task_key') or info.get('uid') or phone or f'account{index}'),
                    'uid': info.get('uid') or phone,
                    'phone': phone,
                    'password': info.get('password')
                })
            else:
                self.logger.warning(f"账号配置不完整，缺少 phone/password：第 {index} 个账号")
        return user_list

    def update_user_uid(self, task_key, uid):
        return None

    def get_cookie(self, uid):
        return None

    def delete_session(self, uid):
        return None

    def save_session(self, uid, cookie_str, user_data):
        return bool(cookie_str)

    def update_cookie(self, uid, cookie_str):
        return bool(cookie_str)

    def get_vip_further_get_time_ms(self, user_uid):
        value = self._load_state().get("vip_further_get_time", {}).get(str(user_uid))
        if value is None:
            return None
        return int(value)

    def set_vip_further_get_time_ms(self, user_uid, ms):
        state = self._load_state()
        state.setdefault("vip_further_get_time", {})[str(user_uid)] = int(ms)
        self._save_state(state)

    def load_send_records(self):
        return self._load_state().get("send_records", {})

    def save_send_records(self, data):
        state = self._load_state()
        state["send_records"] = data
        return self._save_state(state)


def create_storage(logger):
    try:
        storage = RedisStorage(logger)
        logger.info("当前使用 Redis 存储")
        return storage
    except Exception as e:
        logger.warning(f"Redis 不可用，降级使用本地 JSON 存储: {e}")
        return LocalJsonStorage(logger)
