import json
import os
import re
from pkg.plugin.context import register, handler, llm_func, BasePlugin, APIHost, EventContext
from pkg.plugin.events import *
from pkg.platform.types import *
import pkg.platform.types as platform_types

@register(name='FollowMsgPlugin', 
          description='带编辑功能的群消息私聊提醒插件', 
          version='0.1', 
          author="sheetung")
class FollowMsgPlugin(BasePlugin):
    def __init__(self, host: APIHost):
        super().__init__(host)
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.load_configs()
    
    def load_configs(self):
        """加载所有配置文件"""
        # 加载关键词/关注人配置
        try:
            with open(os.path.join(self.script_dir, 'alert_triggers.json'), 'r', encoding='utf-8') as f:
                self.alert_triggers = json.load(f)
        except FileNotFoundError:
            self.alert_triggers = {"keywords": [], "groups": {}, "users": []}  
            self.save_config('alert_triggers.json', self.alert_triggers)
        
        # 加载被提醒人配置
        try:
            with open(os.path.join(self.script_dir, 'alert_recipients.json'), 'r', encoding='utf-8') as f:
                self.alert_recipients = json.load(f)
        except FileNotFoundError:
            self.alert_recipients = {"recipients": []}
            self.save_config('alert_recipients.json', self.alert_recipients)

    def save_config(self, filename, data):
        """保存配置到文件"""
        with open(os.path.join(self.script_dir, filename), 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

    @handler(GroupMessageReceived)
    async def handle_group_message(self, ctx: EventContext):
        msg = str(ctx.event.message_chain).strip()
        sender_id = ctx.event.sender_id
        group_id = ctx.event.launcher_id
        launcher_type = str(ctx.event.launcher_type)

        # 获取黑/白名单
        mode = self.ap.pipeline_cfg.data['access-control']['mode']
        sess_list = self.ap.pipeline_cfg.data['access-control'][mode]

        found = False
        if (launcher_type== 'group' and 'group_*' in sess_list) \
            or (launcher_type == 'person' and 'person_*' in sess_list):
            found = True
        else:
            for sess in sess_list:
                if sess == f"{launcher_type}_{group_id}":
                    found = True
                    break 
        ctn = False
        if mode == 'whitelist':
            ctn = found
        else:
            ctn = not found
        if not ctn:
            # print(f'您被杀了哦')
            return
        
        # 处理 msg，如果包含 / 则删除 /
        if '/' in msg:
            msg = msg.replace('/', '')

        # 首先检查是否是follow命令
        if msg.startswith('follow'):
            print(f'in follow :{msg}')
            await self.process_follow_command(ctx, msg, sender_id, group_id)
            return
        
        # 不是命令则检查触发条件
        await self.check_triggers(ctx, msg, sender_id, group_id)

    async def process_follow_command(self, ctx, msg, sender_id, group_id):
        """处理follow命令"""
        try:
            parts = msg.split()
            if len(parts) < 2:
                await self.show_help(ctx, group_id)
                return
            
            cmd_type = parts[1].lower()
            
            # 新增 help 命令
            if cmd_type == "help":
                await self.show_help(ctx, group_id)
                return
                
            if len(parts) < 3:
                await ctx.send_message(ctx.event.launcher_type, group_id, 
                                      MessageChain(["命令格式错误，正确格式: follow <类型> <参数1> [参数2]"]))
                return
            
            param1 = parts[2]
            param2 = parts[3] if len(parts) > 3 else None
            
            if cmd_type == "私信":
                # follow 私信 <QQ号> - 添加接收者
                if not param1.isdigit():
                    await ctx.send_message(ctx.event.launcher_type, group_id, 
                                          MessageChain(["QQ号必须是数字"]))
                    return
                
                # 添加接收者
                if not any(r["user_id"] == param1 for r in self.alert_recipients["recipients"]):
                    self.alert_recipients["recipients"].append({"user_id": param1})
                    self.save_config('alert_recipients.json', self.alert_recipients)
                    await ctx.send_message(ctx.event.launcher_type, group_id, 
                                          MessageChain(f"已添加接收者 {param1}，当触发条件满足时会收到私聊提醒"))
                else:
                    await ctx.send_message(ctx.event.launcher_type, group_id, 
                                          MessageChain(f"{param1} 已是接收者"))
            
            elif cmd_type == "群号":
                # follow 群号 <群号> <消息或QQ号>
                if not param2:
                    await ctx.send_message(ctx.event.launcher_type, group_id, 
                                          MessageChain(["缺少参数，使用方式: follow 群号 <群号> <消息或QQ号>"]))
                    return
                
                # 初始化群组结构
                if param1 not in self.alert_triggers["groups"]:
                    self.alert_triggers["groups"][param1] = {"keywords": [], "users": []}
                
                # 判断是关注消息还是用户
                extracted_qq = await self.extract_qq(param2) if param2 else None

                print(f'qq={self.extract_qq(param2)}')
                if extracted_qq:
                    # 是QQ号 - 关注用户
                    if param2 not in self.alert_triggers["groups"][param1]["users"]:
                        self.alert_triggers["groups"][param1]["users"].append(extracted_qq)
                    
                    await ctx.send_message(ctx.event.launcher_type, group_id, 
                                          MessageChain(f"已设置关注群 {param1} 的用户 {extracted_qq}"))
                else:
                    # 是关键词 - 关注消息
                    if param2 not in self.alert_triggers["groups"][param1]["keywords"]:
                        self.alert_triggers["groups"][param1]["keywords"].append(param2)
                    
                    await ctx.send_message(ctx.event.launcher_type, group_id, 
                                          MessageChain(f"已设置关注群 {param1} 的关键词 '{param2}'"))
                
                self.save_config('alert_triggers.json', self.alert_triggers)
            
            elif cmd_type == "用户":
                # follow 用户 <QQ号> - 全局关注用户
                if not param1.isdigit():
                    await ctx.send_message(ctx.event.launcher_type, group_id, 
                                          MessageChain(["QQ号必须是数字"]))
                    return
                
                if param1 not in self.alert_triggers["users"]:
                    # 现在users是列表，可以直接append
                    print(f'qqid={param1}')
                    self.alert_triggers["users"].append(str(param1))
                    self.save_config('alert_triggers.json', self.alert_triggers)
                    await ctx.send_message(ctx.event.launcher_type, group_id, 
                                          MessageChain(f"已全局关注用户 {param1} 的所有群消息"))
                else:
                    await ctx.send_message(ctx.event.launcher_type, group_id, 
                                          MessageChain(f"已在全局关注用户 {param1}"))
            
            elif cmd_type == "关键词":
                # follow 关键词 <关键词> - 全局关注关键词
                if not param1:
                    await ctx.send_message(ctx.event.launcher_type, group_id, 
                                          MessageChain(["请输入关键词"]))
                    return
                
                if param1 not in self.alert_triggers["keywords"]:
                    self.alert_triggers["keywords"].append(param1)
                    self.save_config('alert_triggers.json', self.alert_triggers)
                    await ctx.send_message(ctx.event.launcher_type, group_id, 
                                          MessageChain(f"已全局关注关键词 '{param1}'"))
                else:
                    await ctx.send_message(ctx.event.launcher_type, group_id, 
                                          MessageChain(f"已在全局关注关键词 '{param1}'"))
            
            else:
                await ctx.send_message(ctx.event.launcher_type, group_id, 
                                      MessageChain(["未知命令类型，可用: 私信, 群号, 用户, 关键词"]))
        
        except Exception as e:
            await ctx.send_message(ctx.event.launcher_type, group_id, 
                                  MessageChain([f"命令处理错误: {str(e)}"]))
    
    async def check_triggers(self, ctx, msg, sender_id, group_id):
        """检查消息是否符合触发条件"""
        triggers_matched = []
        # 1. 检查全局关键词
        for keyword in self.alert_triggers.get("keywords", []):
            if keyword.lower() in msg.lower():
                triggers_matched.append(("全局关键词", keyword))
        
        # 2. 检查全局用户
        for user_id in self.alert_triggers.get("users", []):
            # print(f'user_id={user_id}/sendid={sender_id}')
            if str(user_id) == str(sender_id):
                triggers_matched.append(("全局用户", user_id))
        
        # 3. 检查群组特定触发
        group_triggers = self.alert_triggers.get("groups", {}).get(str(group_id), {})
        
        # 3.1 检查群关键词
        for keyword in group_triggers.get("keywords", []):
            if keyword.lower() in msg.lower():
                triggers_matched.append((f"群 {group_id} 关键词", keyword))
        
        # 3.2 检查群用户
        for user_id in group_triggers.get("users", []):
            if str(user_id) == str(sender_id):
                triggers_matched.append((f"群 {group_id} 用户", user_id))
        
        # 如果有触发条件匹配，发送提醒给所有接收者
        if triggers_matched and self.alert_recipients["recipients"]:
            for recipient in self.alert_recipients["recipients"]:
                alert_message = []
                alert_message.append("⚠️ 有新的触发消息 ⚠️\n")
                alert_message.append(f"群号: {group_id}\n")
                alert_message.append(f"发送者: {sender_id}\n")
                alert_message.append(f"消息内容: {msg}\n")
                alert_message.append("触发条件:\n")
                
                for trigger_type, trigger in triggers_matched:
                    alert_message.append(f"- {trigger_type}: {trigger}\n")
                
                await self.send_reply(recipient["user_id"], 'person', alert_message)
    async def show_help(self, ctx, group_id):
        """显示帮助信息"""
        help_msg = [
            "📢 FollowMsgPlugin 使用帮助 📢\n",
            "1. 添加接收者:",
            "   follow 私信 <QQ号> - 添加接收提醒的私聊用户\n",
            "2. 设置群组关注规则:",
            "   follow 群号 <群号> <QQ号> - 监控指定群中的特定用户",
            "   follow 群号 <群号> <关键词> - 监控指定群中的关键字\n",
            "3. 设置全局关注规则:",
            "   follow 用户 <QQ号> - 监控该用户在所有群的消息",
            "   follow 关键词 <关键词> - 监控所有群中的关键词\n",
            "4. 其他命令:",
            "   follow help - 显示本帮助信息\n",
            "示例:",
            "   follow 私信 123456789 - 设置123456789为接收者",
            "   follow 群号 987654321 QQ555555 - 监控群987654321中的用户555555",
            "   follow 群号 987654321 重要通知 - 监控群987654321中的'重要通知'关键词"
        ]
        
        await ctx.send_message(ctx.event.launcher_type, group_id, 
                              MessageChain(["\n".join(help_msg)]))
        
    async def send_reply(self, target_id, target_type, messages):
        await self.host.send_active_message(
            adapter=self.host.get_platform_adapters()[0],
            target_type=target_type,
            target_id=str(target_id),
            message=MessageChain(messages),
        )
    
    async def extract_qq(self,qq_str):
            """提取字符串中的纯数字QQ号"""
            if not qq_str:
                return ""
            qq_str = str(qq_str)
            # 移除常见QQ前缀和特殊字符
            qq_str = re.sub(r'(?i)^(qq[:：]?|at)', '', qq_str)
            # 只保留数字并去除前后空格
            return re.sub(r'[^\d]', '', qq_str).strip()
        
    def __del__(self):
        """插件卸载时保存配置"""
        self.save_config('alert_triggers.json', self.alert_triggers)
        self.save_config('alert_recipients.json', self.alert_recipients)
