#!/usr/bin/env bash
# APK neu bauen (nach Web-Aenderungen): cap sync + gradle assembleDebug.
set -euo pipefail
cd "$(dirname "$0")"
export JAVA_HOME="${JAVA_HOME:-$HOME/jdk21}"
export ANDROID_HOME="${ANDROID_HOME:-$HOME/android-sdk}"
export PATH="$HOME/.nvm/versions/node/$(ls $HOME/.nvm/versions/node | tail -1)/bin:$PATH"
npx cap sync android
cd android && ./gradlew assembleDebug --no-daemon
echo "APK: android/app/build/outputs/apk/debug/app-debug.apk"
