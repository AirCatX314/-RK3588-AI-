#!/usr/bin/env python3
"""
邮件通知模块
"""

import smtplib
import os
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from email.header import Header
from datetime import datetime

# 邮件配置
EMAIL_CONFIG = {
    'smtp_server': 'smtp.qq.com',
    'smtp_port': 465,
    'sender': '',
    'password': '',
    'receiver': ''
}

def load_email_config():
    """从配置文件加载邮件配置"""
    config_file = '/home/elf/labsafe/config.json'
    if os.path.exists(config_file):
        try:
            import json
            with open(config_file, 'r') as f:
                config = json.load(f)
                notif = config.get('notifications', {})
                return {
                    'smtp_server': 'smtp.qq.com',
                    'smtp_port': 465,
                    'sender': notif.get('email_sender', ''),
                    'password': notif.get('email_password', ''),
                    'receiver': notif.get('email_addr', '')
                }
        except:
            pass
    return EMAIL_CONFIG

def send_email(subject, content, html=False, image_data=None):
    """发送邮件"""
    config = load_email_config()
    
    if not config['sender'] or not config['receiver']:
        print("邮件配置不完整，无法发送")
        return False
    
    try:
        msg = MIMEMultipart('mixed')
        msg['From'] = config['sender']
        msg['To'] = config['receiver']
        msg['Subject'] = str(Header(f"[LabSafe] {subject}", 'utf-8'))
        
        # 添加HTML内容
        if html:
            html_part = MIMEText(content, 'html', 'utf-8')
            msg.attach(html_part)
        else:
            text_part = MIMEText(content, 'plain', 'utf-8')
            msg.attach(text_part)
        
        # 添加图片
        if image_data:
            img = MIMEImage(image_data, _subtype='jpeg')
            img.add_header('Content-ID', '<camera_snapshot>')
            img.add_header('Content-Disposition', 'inline', filename='snapshot.jpg')
            msg.attach(img)
        
        # 连接SMTP服务器发送 - 尝试 SSL
        try:
            server = smtplib.SMTP_SSL(config['smtp_server'], config['smtp_port'])
            server.login(config['sender'], config['password'])
            server.sendmail(config['sender'], config['receiver'], msg.as_string())
            server.quit()
        except Exception as ssl_err:
            # 如果SSL失败，尝试 STARTTLS
            print(f"SSL方式失败，尝试STARTTLS: {ssl_err}")
            server = smtplib.SMTP(config['smtp_server'], 587)
            server.starttls()
            server.login(config['sender'], config['password'])
            server.sendmail(config['sender'], config['receiver'], msg.as_string())
            server.quit()
        
        print(f"邮件已发送: {subject}")
        return True
    
    except Exception as e:
        print(f"发送邮件失败: {e}")
        return False

def send_security_report(report_content, image_data=None):
    """发送安全分析报告"""
    subject = f"实验室安全分析报告 - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    
    # 如果有图片，添加到HTML中
    img_tag = ''
    if image_data:
        img_tag = '<p><img src="cid:camera_snapshot" style="max-width: 100%; border-radius: 8px;"></p>'
    
    html_content = f"""
    <html>
    <body style="font-family: Arial, sans-serif;">
        <h2 style="color: #d32f2f;">🧪 实验室安全分析报告</h2>
        <p style="color: #666;">生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        <hr>
        {img_tag}
        <pre style="background: #f5f5f5; padding: 15px; border-radius: 5px; white-space: pre-wrap;">{report_content}</pre>
        <hr>
        <p style="color: #999; font-size: 12px;">
            此邮件由LabSafe实验室安全监控系统自动发送
        </p>
    </body>
    </html>
    """
    
    return send_email(subject, html_content, html=True, image_data=image_data)

def send_alert(title, content, image_data=None):
    """发送报警邮件"""
    subject = f"⚠️ 实验室报警 - {title}"
    img_tag = ''
    if image_data:
        img_tag = '<p><img src="cid:camera_snapshot" style="max-width: 100%; border-radius: 8px;"></p>'
    html_content = f"""
    <html>
    <body style="font-family: Arial, sans-serif;">
        <h2 style="color: #d32f2f;">⚠️ 实验室报警</h2>
        <h3 style="color: #f44336;">{title}</h3>
        {img_tag}
        <div>{content}</div>
        <p style="color: #666;">报警时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        <hr>
        <p style="color: #999; font-size: 12px;">
            此邮件由LabSafe实验室安全监控系统自动发送
        </p>
    </body>
    </html>
    """
    
    return send_email(subject, html_content, html=True, image_data=image_data)

if __name__ == "__main__":
    # 测试
    print("邮件模块测试")
    # send_email("测试", "这是一封测试邮件")
