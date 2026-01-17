import io
import base64
import uuid
import pyotp
import qrcode
from nicegui import ui, app
from app.core.config import ADMIN_USER, ADMIN_PASS
from app.core.state import ADMIN_CONFIG
from app.core.data_manager import save_admin_config
from app.utils.common import safe_base64


def login_page():
    # 容器：用于切换登录步骤 (账号密码 -> MFA)
    container = ui.card().classes('absolute-center w-full max-w-sm p-8 shadow-2xl rounded-xl bg-white')

    # --- 步骤 1: 账号密码验证 ---
    def render_step1():
        container.clear()
        with container:
            ui.label('X-Fusion Panel').classes('text-2xl font-extrabold mb-2 w-full text-center text-slate-800')
            ui.label('请登录以继续').classes('text-sm text-gray-400 mb-6 w-full text-center')

            username = ui.input('账号').props('outlined dense').classes('w-full mb-3')
            password = ui.input('密码', password=True).props('outlined dense').classes('w-full mb-6').on(
                'keydown.enter', lambda: check_cred())

            def check_cred():
                if username.value == ADMIN_USER and password.value == ADMIN_PASS:
                    check_mfa()
                else:
                    ui.notify('账号或密码错误', color='negative', position='top')

            ui.button('下一步', on_click=check_cred).classes('w-full bg-slate-900 text-white shadow-lg h-10')
            ui.label('© Powered by X-Fusion').classes(
                'text-xs text-gray-400 mt-6 w-full text-center font-mono opacity-80')

    # --- 步骤 2: MFA 验证或设置 ---
    def check_mfa():
        secret = ADMIN_CONFIG.get('mfa_secret')
        if not secret:
            new_secret = pyotp.random_base32()
            render_setup(new_secret)
        else:
            render_verify(secret)

    def render_setup(secret):
        container.clear()
        totp_uri = pyotp.totp.TOTP(secret).provisioning_uri(name=ADMIN_USER, issuer_name="X-Fusion Panel")
        qr = qrcode.make(totp_uri)
        img_buffer = io.BytesIO()
        qr.save(img_buffer, format='PNG')
        img_b64 = base64.b64encode(img_buffer.getvalue()).decode('utf-8')

        with container:
            ui.label('绑定二次验证 (MFA)').classes('text-xl font-bold mb-2 w-full text-center')
            ui.image(f'data:image/png;base64,{img_b64}').style('width: 180px; height: 180px').classes('mx-auto')
            ui.label(secret).classes('text-xs font-mono text-gray-600 text-center w-full mb-4')
            code = ui.input('验证码', placeholder='6位数字').props('outlined dense input-class=text-center').classes(
                'w-full mb-4')

            async def confirm():
                if pyotp.TOTP(secret).verify(code.value):
                    ADMIN_CONFIG['mfa_secret'] = secret
                    await save_admin_config()
                    ui.notify('绑定成功', type='positive')
                    finish()
                else:
                    ui.notify('验证码错误', type='negative')

            ui.button('确认绑定', on_click=confirm).classes('w-full bg-green-600 text-white h-10')

    def render_verify(secret):
        container.clear()
        with container:
            ui.label('安全验证').classes('text-xl font-bold mb-6 w-full text-center')
            code = ui.input(placeholder='------').props(
                'outlined input-class=text-center text-xl tracking-widest').classes('w-full mb-6')
            code.on('keydown.enter', lambda: verify())
            ui.timer(0.1, lambda: ui.run_javascript('document.querySelector(".q-field__native").focus()'), once=True)

            def verify():
                if pyotp.TOTP(secret).verify(code.value):
                    finish()
                else:
                    ui.notify('无效验证码', type='negative')
                    code.value = ''

            ui.button('验证登录', on_click=verify).classes('w-full bg-slate-900 text-white h-10')
            ui.button('返回', on_click=render_step1).props('flat dense').classes('w-full mt-2 text-gray-400 text-xs')

    def finish():
        app.storage.user['authenticated'] = True
        # 生成会话版本号
        if 'session_version' not in ADMIN_CONFIG:
            ADMIN_CONFIG['session_version'] = str(uuid.uuid4())[:8]
        app.storage.user['session_version'] = ADMIN_CONFIG['session_version']
        ui.navigate.to('/')

    render_step1()