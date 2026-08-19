"""
GlassChat - 蓝牙点对点直连聊天
功能：打开蓝牙 → 列出已配对设备 → 点对点直连
特性：4页引导 + 登录/注册(抽屉+验证码) + PBKDF2密码哈希 + iOS毛玻璃UI
"""
from kivy.app import App
from kivy.uix.screenmanager import ScreenManager, Screen, SlideTransition
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.scrollview import ScrollView
from kivy.animation import Animation
from kivy.graphics import Color, RoundedRectangle, Rectangle
from kivy.core.window import Window
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.properties import NumericProperty, StringProperty, ListProperty, BooleanProperty
import threading
import json
import random
import string
import os
import base64
import hashlib
import hmac
from datetime import datetime
from pathlib import Path

Window.clearcolor = (0.1, 0.15, 0.25, 1)

try:
    from jnius import autoclass
    ANDROID = True
except ImportError:
    ANDROID = False

ACCOUNTS_FILE = '/sdcard/GlassChatApp/accounts.json'


def hash_password(password):
    """用 PBKDF2 哈希密码（100000轮迭代）"""
    salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
    return base64.b64encode(salt + key).decode('utf-8')


def verify_password(password, hashed):
    """验证密码"""
    try:
        decoded = base64.b64decode(hashed.encode('utf-8'))
        salt = decoded[:32]
        stored_key = decoded[32:]
        new_key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
        return hmac.compare_digest(stored_key, new_key)
    except:
        return False


def load_accounts():
    try:
        if Path(ACCOUNTS_FILE).exists():
            with open(ACCOUNTS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except:
        pass
    return {}


def save_accounts(accounts):
    try:
        Path(ACCOUNTS_FILE).parent.mkdir(parents=True, exist_ok=True)
        with open(ACCOUNTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(accounts, f, ensure_ascii=False, indent=2)
    except:
        pass


def request_bluetooth_permissions():
    """请求蓝牙运行时权限"""
    if not ANDROID:
        return
    try:
        from android.permissions import request_permissions, Permission
        Build = autoclass('android.os.Build')
        if Build.VERSION.SDK_INT >= 31:
            perms = [
                'android.permission.BLUETOOTH_CONNECT',
                'android.permission.BLUETOOTH_SCAN',
                'android.permission.ACCESS_FINE_LOCATION',
            ]
        else:
            perms = [
                Permission.BLUETOOTH,
                Permission.BLUETOOTH_ADMIN,
                Permission.ACCESS_FINE_LOCATION,
            ]
        request_permissions(perms)
    except Exception:
        pass


class BluetoothManager:
    """蓝牙点对点通信管理器（RFCOMM / SPP）"""
    SPP_UUID = '00001101-0000-1000-8000-00805f9b34fb'

    def __init__(self):
        self.adapter = None
        self.server_socket = None
        self.socket = None
        self.input_stream = None
        self.output_stream = None
        self.reader = None
        self.connected_name = ''
        self.running = False
        self.on_message = None
        self.on_status = None
        if ANDROID:
            try:
                BluetoothAdapter = autoclass('android.bluetooth.BluetoothAdapter')
                self.adapter = BluetoothAdapter.getDefaultAdapter()
                self.BluetoothDevice = autoclass('android.bluetooth.BluetoothDevice')
                self.UUID = autoclass('java.util.UUID')
                self.BufferedReader = autoclass('java.io.BufferedReader')
                self.InputStreamReader = autoclass('java.io.InputStreamReader')
            except Exception:
                self.adapter = None

    def is_available(self):
        return ANDROID and self.adapter is not None

    def is_enabled(self):
        try:
            return self.adapter.isEnabled()
        except:
            return False

    def enable(self):
        try:
            self.adapter.enable()
            return True
        except:
            return False

    def get_name(self):
        try:
            return self.adapter.getName()
        except:
            return '未知设备'

    def get_address(self):
        try:
            return self.adapter.getAddress()
        except:
            return '--:--:--:--:--:--'

    def get_bonded_devices(self):
        """获取已配对设备列表"""
        if not self.is_available():
            return []
        result = []
        try:
            devices = self.adapter.getBondedDevices()
            it = devices.iterator()
            while it.hasNext():
                d = it.next()
                result.append((d.getName(), d.getAddress()))
        except:
            pass
        return result

    def cancel_discovery(self):
        try:
            self.adapter.cancelDiscovery()
        except:
            pass

    def listen(self):
        """作为服务端监听连接"""
        if not self.is_available():
            self._notify_status('蓝牙不可用')
            return
        self.running = True
        try:
            self.cancel_discovery()
            uuid = self.UUID.fromString(self.SPP_UUID)
            self.server_socket = self.adapter.listenUsingRfcommWithServiceRecord('GlassChat', uuid)
            self._notify_status('正在监听，等待对方连接...')
            sock = self.server_socket.accept()
            self._setup_connection(sock)
        except Exception as e:
            self._notify_status(f'监听失败: {e}')

    def connect(self, address):
        """作为客户端连接对方设备"""
        if not self.is_available():
            self._notify_status('蓝牙不可用')
            return
        self.running = True
        try:
            self.cancel_discovery()
            device = self.adapter.getRemoteDevice(address)
            uuid = self.UUID.fromString(self.SPP_UUID)
            sock = device.createRfcommSocketToServiceRecord(uuid)
            sock.connect()
            self.connected_name = device.getName()
            self._setup_connection(sock)
        except Exception as e:
            self._notify_status(f'连接失败: {e}')

    def _setup_connection(self, sock):
        """建立连接后的读写流"""
        self.socket = sock
        self.input_stream = sock.getInputStream()
        self.output_stream = sock.getOutputStream()
        self.reader = self.BufferedReader(self.InputStreamReader(self.input_stream, 'UTF-8'))
        self._notify_status(f'已连接: {self.connected_name}')
        threading.Thread(target=self._read_loop, daemon=True).start()

    def _read_loop(self):
        """接收消息循环"""
        while self.running:
            try:
                line = self.reader.readLine()
                if line is None:
                    break
                data = json.loads(line)
                if self.on_message:
                    self.on_message(data)
            except:
                break
        self._notify_status('连接已断开')

    def send(self, msg_dict):
        """发送一条 JSON 消息"""
        if not self.output_stream:
            return False
        try:
            line = (json.dumps(msg_dict, ensure_ascii=False) + '\n').encode('utf-8')
            self.output_stream.write(bytearray(line))
            self.output_stream.flush()
            return True
        except:
            return False

    def close(self):
        self.running = False
        try:
            if self.socket:
                self.socket.close()
        except:
            pass
        try:
            if self.server_socket:
                self.server_socket.close()
        except:
            pass

    def _notify_status(self, text):
        if self.on_status:
            self.on_status(text)


class RoundedButton(Button):
    """胶囊圆角按钮"""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.background_normal = ''
        self.background_down = ''
        with self.canvas.before:
            self.btn_color = Color(0.3, 0.6, 1, 0.6)
            self.btn_rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(25)])
        self.bind(pos=self.update_rect, size=self.update_rect)
    
    def update_rect(self, *args):
        self.btn_rect.pos = self.pos
        self.btn_rect.size = self.size


class RoundedTextInput(TextInput):
    """圆角输入框"""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.background_normal = ''
        self.background_active = ''
        self.padding = [dp(20), dp(12)]
        with self.canvas.before:
            Color(1, 1, 1, 0.15)
            self.input_rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(20)])
        self.bind(pos=self.update_rect, size=self.update_rect)
    
    def update_rect(self, *args):
        self.input_rect.pos = self.pos
        self.input_rect.size = self.size


class GlassWidget(FloatLayout):
    """毛玻璃容器"""
    blur_amount = NumericProperty(1.0)
    glass_color = ListProperty([1, 1, 1, 0.1])
    corner_radius = NumericProperty(20)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        with self.canvas.before:
            self.glass_color_instruction = Color(*self.glass_color)
            self.bg_rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(self.corner_radius)])
        self.bind(pos=self.update_rect, size=self.update_rect)
        self.bind(glass_color=self.update_glass_color, corner_radius=self.update_corner_radius)

    def update_rect(self, *args):
        self.bg_rect.pos = self.pos
        self.bg_rect.size = self.size

    def update_glass_color(self, instance, value):
        self.glass_color_instruction.rgba = value

    def update_corner_radius(self, instance, value):
        self.bg_rect.radius = [dp(value)]


class CaptchaWidget(BoxLayout):
    """人机验证码组件"""
    captcha_text = StringProperty('')

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = 'horizontal'
        self.size_hint_y = None
        self.height = dp(50)
        self.spacing = dp(10)

        self.captcha_display = GlassWidget(size_hint_x=0.5, corner_radius=15)
        self.captcha_display.glass_color = [1, 1, 1, 0.25]
        self.captcha_label = Label(text='', font_size=dp(24), bold=True, color=(1, 1, 1, 1))
        self.captcha_display.add_widget(self.captcha_label)

        refresh_btn = RoundedButton(text='🔄', size_hint_x=0.2, font_size=dp(20))
        refresh_btn.bind(on_press=lambda x: self.generate_captcha())

        self.captcha_input = RoundedTextInput(
            hint_text='验证码', size_hint_x=0.3, multiline=False,
            font_size=dp(16), foreground_color=(1, 1, 1, 1)
        )

        self.add_widget(self.captcha_display)
        self.add_widget(self.captcha_input)
        self.add_widget(refresh_btn)
        self.generate_captcha()

    def generate_captcha(self):
        self.captcha_text = ''.join(random.choices(string.digits, k=4))
        self.captcha_label.text = '  '.join(self.captcha_text)

    def verify(self):
        return self.captcha_input.text.strip() == self.captcha_text


class OnboardingPage(Screen):
    """单页引导内容"""
    def __init__(self, page_num, title_text, desc_text, icon_text, **kwargs):
        super().__init__(**kwargs)
        self.page_num = page_num
        
        layout = FloatLayout()
        with layout.canvas.before:
            Color(0.15, 0.25, 0.45, 1)
            self.bg = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self.update_bg, size=self.update_bg)

        glass_container = GlassWidget(
            size_hint=(0.85, 0.7),
            pos_hint={'center_x': 0.5, 'center_y': 0.5}
        )

        content = BoxLayout(orientation='vertical', padding=dp(30), spacing=dp(15))

        # 图标
        icon = Label(
            text=icon_text, font_size=dp(80), color=(0.5, 0.8, 1, 1),
            size_hint_y=0.25
        )

        # 标题
        title = Label(
            text=title_text, font_size=dp(32), color=(1, 1, 1, 1),
            bold=True, size_hint_y=0.15
        )

        # 描述
        desc = Label(
            text=desc_text, font_size=dp(16), color=(1, 1, 1, 0.85),
            halign='center', size_hint_y=0.35
        )

        # 进度指示器
        dots_layout = BoxLayout(size_hint_y=0.1, spacing=dp(10))
        for i in range(4):
            dot = Label(
                text='●' if i == page_num else '○',
                font_size=dp(18),
                color=(0.5, 0.8, 1, 1) if i == page_num else (1, 1, 1, 0.4)
            )
            dots_layout.add_widget(dot)

        # 按钮
        btn = RoundedButton(
            text='继续' if page_num < 3 else '立即开始',
            size_hint=(0.6, None), height=dp(50),
            pos_hint={'center_x': 0.5}
        )
        btn.bind(on_press=self.next_page)

        content.add_widget(icon)
        content.add_widget(title)
        content.add_widget(desc)
        content.add_widget(dots_layout)
        content.add_widget(btn)

        glass_container.add_widget(content)
        layout.add_widget(glass_container)
        self.add_widget(layout)
        self.glass_container = glass_container

    def on_enter(self):
        """每次进入页面都执行轻微上浮淡入"""
        Animation.cancel_all(self.glass_container)
        target_y = self.glass_container.y
        self.glass_container.opacity = 0
        self.glass_container.y = target_y - dp(50)
        Clock.schedule_once(lambda dt: self.animate_entrance(self.glass_container), 0)

    def update_bg(self, *args):
        self.bg.pos = self.pos
        self.bg.size = self.size

    def animate_entrance(self, widget):
        anim = Animation(opacity=1, y=widget.y + dp(50), duration=0.8, t='out_expo')
        anim.start(widget)

    def next_page(self, instance):
        if self.page_num < 3:
            self.manager.current = f'onboarding{self.page_num + 1}'
        else:
            self.blur_transition()
            Clock.schedule_once(lambda dt: setattr(self.manager, 'current', 'auth'), 0.3)

    def blur_transition(self):
        for child in self.walk():
            if isinstance(child, (Label, Button)):
                anim = Animation(opacity=0, duration=0.3)
                anim.start(child)


class AuthScreen(Screen):
    """登录/注册 - 抽屉式切换"""
    is_register_mode = BooleanProperty(False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.accounts = load_accounts()

        layout = FloatLayout()
        with layout.canvas.before:
            Color(0.15, 0.25, 0.45, 1)
            self.bg = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self.update_bg, size=self.update_bg)

        self.main_container = GlassWidget(
            size_hint=(0.9, None), height=dp(430),
            pos_hint={'center_x': 0.5, 'center_y': 0.5}
        )

        self.content = BoxLayout(orientation='vertical', padding=dp(25), spacing=dp(12))

        self.title_label = Label(
            text='登录', font_size=dp(32), color=(1, 1, 1, 1),
            size_hint_y=None, height=dp(45)
        )

        username_label = Label(
            text='用户名', size_hint_y=None, height=dp(26),
            color=(1, 1, 1, 0.9), font_size=dp(15)
        )
        self.username_input = RoundedTextInput(
            hint_text='输入用户名', size_hint_y=None, height=dp(48),
            multiline=False, font_size=dp(16), foreground_color=(1, 1, 1, 1)
        )

        password_label = Label(
            text='密码', size_hint_y=None, height=dp(26),
            color=(1, 1, 1, 0.9), font_size=dp(15)
        )
        self.password_input = RoundedTextInput(
            hint_text='输入密码', size_hint_y=None, height=dp(48),
            multiline=False, password=True, font_size=dp(16), foreground_color=(1, 1, 1, 1)
        )

        # 验证码区域（注册时抽屉弹出）
        self.captcha_container = BoxLayout(
            orientation='vertical', size_hint_y=None, height=0,
            opacity=0, spacing=dp(8)
        )
        captcha_label = Label(
            text='人机验证', size_hint_y=None, height=dp(24),
            color=(1, 1, 1, 0.9), font_size=dp(15)
        )
        self.captcha_widget = CaptchaWidget()
        self.captcha_container.add_widget(captcha_label)
        self.captcha_container.add_widget(self.captcha_widget)

        self.error_label = Label(
            text='', size_hint_y=None, height=dp(26),
            color=(1, 0.3, 0.3, 1), font_size=dp(14)
        )

        btn_layout = BoxLayout(orientation='horizontal', size_hint_y=None, height=dp(48), spacing=dp(15))
        self.main_btn = RoundedButton(text='登录', size_hint_x=0.6)
        self.main_btn.bind(on_press=self.handle_auth)

        self.switch_btn = Button(
            text='注册账号', size_hint_x=0.4,
            background_color=(0, 0, 0, 0), color=(0.5, 0.8, 1, 1), font_size=dp(14)
        )
        self.switch_btn.bind(on_press=self.toggle_mode)

        btn_layout.add_widget(self.main_btn)
        btn_layout.add_widget(self.switch_btn)

        self.content.add_widget(self.title_label)
        self.content.add_widget(username_label)
        self.content.add_widget(self.username_input)
        self.content.add_widget(password_label)
        self.content.add_widget(self.password_input)
        self.content.add_widget(self.captcha_container)
        self.content.add_widget(self.error_label)
        self.content.add_widget(btn_layout)

        self.main_container.add_widget(self.content)
        layout.add_widget(self.main_container)
        self.add_widget(layout)

        self.main_container.opacity = 0
        self.main_container.y -= dp(80)
        Clock.schedule_once(lambda dt: self.animate_entrance(), 0.2)

    def update_bg(self, *args):
        self.bg.pos = self.pos
        self.bg.size = self.size

    def animate_entrance(self):
        anim = Animation(opacity=1, y=self.main_container.y + dp(80), duration=1.0, t='out_expo')
        anim.start(self.main_container)

    def toggle_mode(self, instance):
        """抽屉式切换登录/注册"""
        self.is_register_mode = not self.is_register_mode
        if self.is_register_mode:
            self.title_label.text = '注册'
            self.main_btn.text = '注册'
            self.switch_btn.text = '已有账号？登录'
            anim_container = Animation(height=dp(560), duration=0.5, t='out_expo')
            anim_container.start(self.main_container)
            anim_captcha = Animation(height=dp(88), opacity=1, duration=0.5, t='out_expo')
            anim_captcha.start(self.captcha_container)
        else:
            self.title_label.text = '登录'
            self.main_btn.text = '登录'
            self.switch_btn.text = '注册账号'
            anim_captcha = Animation(height=0, opacity=0, duration=0.4, t='in_expo')
            anim_captcha.start(self.captcha_container)
            anim_container = Animation(height=dp(430), duration=0.4, t='in_expo')
            Clock.schedule_once(lambda dt: anim_container.start(self.main_container), 0.1)
        self.error_label.text = ''

    def handle_auth(self, instance):
        username = self.username_input.text.strip()
        password = self.password_input.text.strip()

        if not username or not password:
            self.error_label.text = '用户名和密码不能为空'
            return

        if self.is_register_mode:
            if not self.captcha_widget.verify():
                self.error_label.text = '验证码错误'
                self.captcha_widget.generate_captcha()
                self.captcha_widget.captcha_input.text = ''
                return
            if username in self.accounts:
                self.error_label.text = '用户名已存在'
                return
            self.accounts[username] = {
                'password': hash_password(password),
                'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            save_accounts(self.accounts)
            self.go_to_setup(username)
        else:
            if username not in self.accounts:
                self.error_label.text = '用户名不存在'
                return
            if not verify_password(password, self.accounts[username]['password']):
                self.error_label.text = '密码错误'
                return
            self.go_to_setup(username)

    def go_to_setup(self, username):
        app = App.get_running_app()
        app.username = username
        setup_screen = self.manager.get_screen('setup')
        setup_screen.username = username
        self.blur_transition()
        Clock.schedule_once(lambda dt: setattr(self.manager, 'current', 'setup'), 0.3)

    def blur_transition(self):
        for child in self.walk():
            if isinstance(child, (Label, Button)):
                anim = Animation(opacity=0, duration=0.3)
                anim.start(child)


class SetupScreen(Screen):
    """蓝牙连接设置页"""
    username = StringProperty('User')

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        layout = FloatLayout()

        with layout.canvas.before:
            Color(0.15, 0.25, 0.45, 1)
            self.bg = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self.update_bg, size=self.update_bg)

        glass_container = GlassWidget(
            size_hint=(0.9, 0.85),
            pos_hint={'center_x': 0.5, 'center_y': 0.5}
        )

        content = BoxLayout(orientation='vertical', padding=dp(20), spacing=dp(8))

        title = Label(text='蓝牙连接', font_size=dp(30), color=(1, 1, 1, 1), size_hint_y=None, height=dp(45))
        self.name_label = Label(text='', size_hint_y=None, height=dp(28), color=(0.5, 0.8, 1, 1), font_size=dp(16))
        self.bt_status = Label(text='', size_hint_y=None, height=dp(28), color=(1, 1, 1, 0.8), font_size=dp(14))

        dev_title = Label(text='已配对设备（点击连接）', size_hint_y=None, height=dp(24), color=(1, 1, 1, 0.9), font_size=dp(14))
        self.device_scroll = ScrollView(size_hint=(1, None), height=dp(220))
        self.device_layout = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(6))
        self.device_layout.bind(minimum_height=self.device_layout.setter('height'))
        self.device_scroll.add_widget(self.device_layout)

        btn_col = BoxLayout(orientation='vertical', size_hint_y=None, height=dp(150), spacing=dp(10))
        self.enable_btn = RoundedButton(text='开启蓝牙', size_hint_y=None, height=dp(44))
        self.enable_btn.bind(on_press=self.enable_bluetooth)

        refresh_btn = RoundedButton(text='刷新设备列表', size_hint_y=None, height=dp(44))
        refresh_btn.bind(on_press=lambda x: self.refresh_devices())

        listen_btn = RoundedButton(text='📡 开始监听（等待对方连接）', size_hint_y=None, height=dp(44))
        listen_btn.bind(on_press=self.start_listen)

        btn_col.add_widget(self.enable_btn)
        btn_col.add_widget(refresh_btn)
        btn_col.add_widget(listen_btn)

        content.add_widget(title)
        content.add_widget(self.name_label)
        content.add_widget(self.bt_status)
        content.add_widget(dev_title)
        content.add_widget(self.device_scroll)
        content.add_widget(btn_col)

        glass_container.add_widget(content)
        layout.add_widget(glass_container)
        self.add_widget(layout)

        glass_container.opacity = 0
        glass_container.y -= dp(80)
        Clock.schedule_once(lambda dt: self.animate_entrance(glass_container), 0.2)
        self.bind(username=self.update_name_display)

    def on_enter(self):
        self.refresh_devices()

    def update_bg(self, *args):
        self.bg.pos = self.pos
        self.bg.size = self.size

    def update_name_display(self, *args):
        self.name_label.text = f'👤 {self.username}'

    def animate_entrance(self, widget):
        anim = Animation(opacity=1, y=widget.y + dp(80), duration=1.0, t='out_expo')
        anim.start(widget)

    def get_bt(self):
        return self.manager.get_screen('chat').bt

    def enable_bluetooth(self, instance):
        bt = self.get_bt()
        if not bt.is_available():
            self.bt_status.text = '蓝牙不可用（请在 Android 手机上运行）'
            return
        if bt.is_enabled():
            self.bt_status.text = f'蓝牙已开启：{bt.get_name()}'
        else:
            if bt.enable():
                self.bt_status.text = '正在开启蓝牙...'
                Clock.schedule_once(lambda dt: self.refresh_devices(), 1.5)
            else:
                self.bt_status.text = '开启失败，请在系统设置中打开蓝牙'

    def refresh_devices(self, *args):
        bt = self.get_bt()
        if not bt.is_available():
            self.bt_status.text = '蓝牙不可用（请在 Android 手机上运行）'
            return
        if bt.is_enabled():
            self.bt_status.text = f'本机蓝牙：{bt.get_name()} ({bt.get_address()})'
        else:
            self.bt_status.text = '蓝牙未开启，请先开启蓝牙'
        self.device_layout.clear_widgets()
        devices = bt.get_bonded_devices()
        if not devices:
            empty = Label(
                text='暂无已配对设备\n请先在系统蓝牙设置中配对对方设备',
                size_hint_y=None, height=dp(60), color=(1, 1, 1, 0.6), font_size=dp(14)
            )
            self.device_layout.add_widget(empty)
            return
        for name, addr in devices:
            dev_btn = Button(
                text=f'🔹 {name}\n{addr}', size_hint_y=None, height=dp(60),
                background_color=(0.3, 0.5, 0.9, 0.35), color=(1, 1, 1, 1),
                font_size=dp(15), halign='center', valign='middle'
            )
            dev_btn.bind(on_press=lambda x, n=name, a=addr: self.connect_device(n, a))
            self.device_layout.add_widget(dev_btn)

    def connect_device(self, name, addr):
        chat = self.manager.get_screen('chat')
        chat.attach_bluetooth()
        chat.connect_to(name, addr)
        self.blur_transition()
        Clock.schedule_once(lambda dt: setattr(self.manager, 'current', 'chat'), 0.3)

    def start_listen(self, instance):
        chat = self.manager.get_screen('chat')
        chat.attach_bluetooth()
        chat.start_listen()
        self.blur_transition()
        Clock.schedule_once(lambda dt: setattr(self.manager, 'current', 'chat'), 0.3)

    def blur_transition(self):
        for child in self.walk():
            if isinstance(child, (Label, Button)):
                anim = Animation(opacity=0, duration=0.3)
                anim.start(child)


class ChatMessage(BoxLayout):
    """消息气泡"""
    def __init__(self, message, is_self=False, **kwargs):
        super().__init__(**kwargs)
        self.orientation = 'horizontal'
        self.size_hint_y = None
        self.height = dp(70)
        self.padding = [dp(10), dp(5)]
        self.spacing = dp(10)

        if not is_self:
            self.add_widget(Label(size_hint_x=0.1))

        bubble = GlassWidget(size_hint=(0.7, None), height=dp(60), corner_radius=25)
        bubble.glass_color = [0.3, 0.6, 1, 0.3] if is_self else [1, 1, 1, 0.2]

        msg_label = Label(
            text=message, color=(1, 1, 1, 1),
            padding=[dp(15), dp(10)], size_hint=(1, 1), font_size=dp(15)
        )
        bubble.add_widget(msg_label)
        self.add_widget(bubble)

        if is_self:
            self.add_widget(Label(size_hint_x=0.1))


class ChatScreen(Screen):
    """聊天界面"""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bt = None
        self.connected_name = ''
        self.username = 'User'

        layout = BoxLayout(orientation='vertical')

        top_bar = GlassWidget(size_hint=(1, 0.1), corner_radius=0)
        top_bar.glass_color = [1, 1, 1, 0.15]

        top_content = BoxLayout(padding=dp(10))
        self.status_label = Label(text='蓝牙聊天', color=(1, 1, 1, 0.9), font_size=dp(18))
        top_content.add_widget(self.status_label)
        top_bar.add_widget(top_content)

        self.scroll_view = ScrollView(size_hint=(1, 0.75))
        self.messages_layout = BoxLayout(
            orientation='vertical', size_hint_y=None, spacing=dp(5), padding=dp(10)
        )
        self.messages_layout.bind(minimum_height=self.messages_layout.setter('height'))
        self.scroll_view.add_widget(self.messages_layout)

        bottom_bar = GlassWidget(size_hint=(1, 0.15), corner_radius=0)
        bottom_bar.glass_color = [1, 1, 1, 0.15]

        input_layout = BoxLayout(padding=dp(10), spacing=dp(10))

        self.message_input = RoundedTextInput(
            hint_text='输入消息...', size_hint_x=0.75,
            multiline=False, font_size=dp(16), foreground_color=(1, 1, 1, 1)
        )
        self.message_input.bind(on_text_validate=self.send_message)

        send_btn = RoundedButton(text='发送', size_hint_x=0.25)
        send_btn.bind(on_press=self.send_message)

        input_layout.add_widget(self.message_input)
        input_layout.add_widget(send_btn)
        bottom_bar.add_widget(input_layout)

        layout.add_widget(top_bar)
        layout.add_widget(self.scroll_view)
        layout.add_widget(bottom_bar)
        self.add_widget(layout)

    def attach_bluetooth(self):
        app = App.get_running_app()
        self.bt = app.bt
        self.bt.on_message = self._on_bt_message
        self.bt.on_status = self._on_bt_status
        self.username = app.username

    def connect_to(self, name, addr):
        self.connected_name = name
        self.status_label.text = f'正在连接 {name}...'
        threading.Thread(target=self.bt.connect, args=(addr,), daemon=True).start()

    def start_listen(self):
        self.status_label.text = '正在监听，等待对方连接...'
        threading.Thread(target=self.bt.listen, daemon=True).start()

    def _on_bt_message(self, data):
        Clock.schedule_once(
            lambda dt: self.add_message(f"{data.get('user', '对方')}: {data.get('text', '')}", is_self=False), 0
        )

    def _on_bt_status(self, text):
        Clock.schedule_once(lambda dt: setattr(self.status_label, 'text', text), 0)

    def send_message(self, instance):
        text = self.message_input.text.strip()
        if not text:
            return
        self.add_message(f'你: {text}', is_self=True)
        self.message_input.text = ''
        if self.bt and self.bt.send({
            'user': self.username,
            'text': text,
            'time': datetime.now().strftime('%H:%M')
        }):
            pass
        else:
            self.add_system_message('未连接或发送失败')

    def add_message(self, text, is_self=False):
        msg = ChatMessage(text, is_self=is_self)
        msg.opacity = 0
        self.messages_layout.add_widget(msg)
        anim = Animation(opacity=1, duration=0.4, t='out_quad')
        anim.start(msg)
        Clock.schedule_once(lambda dt: setattr(self.scroll_view, 'scroll_y', 0), 0.1)

    def add_system_message(self, text):
        label = Label(
            text=text, size_hint_y=None, height=dp(30),
            color=(1, 1, 1, 0.5), font_size=dp(14)
        )
        self.messages_layout.add_widget(label)


class GlassChatApp(App):
    def build(self):
        self.bt = BluetoothManager()
        self.username = 'User'
        request_bluetooth_permissions()

        sm = ScreenManager(transition=SlideTransition(direction='left', duration=0.35))
        
        # 4页引导
        sm.add_widget(OnboardingPage(
            0, '欢迎使用 GlassChat',
            '一款优雅的点对点蓝牙聊天应用\n无需服务器，无需网络\n打开蓝牙，即刻开聊',
            '💬', name='onboarding0'
        ))
        sm.add_widget(OnboardingPage(
            1, '端到端加密通信',
            '您的消息通过蓝牙直接发送到对方设备\n不经过任何服务器或第三方中转\n蓝牙配对后建立加密安全链路',
            '🔒', name='onboarding1'
        ))
        sm.add_widget(OnboardingPage(
            2, '真正的点对点直连',
            '基于 RFCOMM/SPP 蓝牙协议\n局域网内设备间高速传输\n无需互联网，完全离线可用',
            '📡', name='onboarding2'
        ))
        sm.add_widget(OnboardingPage(
            3, '隐私至上',
            '所有账号数据存储在本地\n密码采用 PBKDF2 算法哈希保护\n不会上传到任何云端服务器',
            '🔐', name='onboarding3'
        ))
        
        sm.add_widget(AuthScreen(name='auth'))
        sm.add_widget(SetupScreen(name='setup'))
        sm.add_widget(ChatScreen(name='chat'))
        
        return sm


if __name__ == '__main__':
    GlassChatApp().run()