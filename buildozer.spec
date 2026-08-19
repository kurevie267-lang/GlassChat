[app]
title = GlassChat
package.name = glasschat
package.domain = com.glasschat

source.dir = .
source.include_exts = py,png,jpg,kv,atlas

version = 1.0

requirements = python3,kivy==2.3.0,pyjnius

orientation = portrait
fullscreen = 0

# Android 12+ requests BLUETOOTH_CONNECT and BLUETOOTH_SCAN at runtime.
# Older Android versions use the legacy Bluetooth permissions.
android.permissions = BLUETOOTH,BLUETOOTH_ADMIN,BLUETOOTH_CONNECT,BLUETOOTH_SCAN,BLUETOOTH_ADVERTISE,ACCESS_FINE_LOCATION,INTERNET
android.api = 31
android.minapi = 21
android.ndk = 25b
android.accept_sdk_license = True

android.archs = arm64-v8a,armeabi-v7a

# 图标和启动图
#icon.filename = %(source.dir)s/icon.png
#presplash.filename = %(source.dir)s/presplash.png

android.gradle_dependencies = 

[buildozer]
log_level = 2
warn_on_root = 0
