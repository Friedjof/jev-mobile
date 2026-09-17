#!/usr/bin/env bash
# Real-device durability acceptance for the local Jev Mobile fixture only.
# It never clears data from a third-party application.  Each test drives the
# fixture exclusively through the normal durable agent/UI path.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cli="$root/.venv/bin/jev-mobile"
db="${JEV_MOBILE_DB:-$HOME/.local/share/jev-mobile/jev-mobile.db}"
serial="${JEV_MOBILE_DEVICE:-$(adb devices | awk '$2 == "device" { print $1; exit }')}"
fixture="io.jev.mobile.fixture"

if [[ -z "$serial" ]]; then
  echo "No connected ADB device." >&2
  exit 1
fi

cleanup() {
  systemctl --user unset-environment JEV_MOBILE_FAULT_POINT JEV_MOBILE_FAULT_FAMILY JEV_MOBILE_FAULT_ONCE JEV_MOBILE_FAULT_AFTER_MUTATION_BEGIN || true
  systemctl --user restart jev-mobile-worker || true
}
trap cleanup EXIT

task_status() {
  JEV_MOBILE_DB="$db" "$cli" task get "$1" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])'
}

wait_task() {
  local id="$1" status
  for _ in $(seq 1 60); do
    status="$(task_status "$id")"
    case "$status" in
      succeeded) return 0 ;;
      failed|cancelled|waiting_for_user) echo "Task $id ended as $status" >&2; return 1 ;;
    esac
    sleep 2
  done
  echo "Task $id did not finish in time" >&2
  return 1
}

start() {
  JEV_MOBILE_DB="$db" "$cli" task start "$1" | awk '{print $1}'
}

prepare_fixture() {
  (cd "$root/android/jev-mobile-bridge" && ./gradlew :test-app:assembleDebug >/dev/null)
  adb -s "$serial" install -r "$root/android/jev-mobile-test-app/build/outputs/apk/debug/test-app-debug.apk" >/dev/null
  # Test fixture data only; never use pm clear for a real target app.
  adb -s "$serial" shell pm clear "$fixture" >/dev/null
  adb -s "$serial" shell monkey -p "$fixture" 1 >/dev/null
}

faulted_task() {
  local family="$1" title="$2" body="$3" id event
  systemctl --user set-environment JEV_MOBILE_FAULT_POINT=after_device_execute JEV_MOBILE_FAULT_FAMILY="$family" JEV_MOBILE_FAULT_ONCE=1
  systemctl --user restart jev-mobile-worker
  id="$(start "In the Jev Mobile Fixture, create an item titled $title with body $body")"
  for _ in $(seq 1 20); do
    event="$(sqlite3 "$db" "select count(*) from task_events where task_id='$id' and event_type='FAULT_INJECTED' and json_extract(payload, '$.mutation_family')='$family' and json_extract(payload, '$.fault_point')='after_device_execute';")"
    [[ "$event" == "1" ]] && break
    sleep 1
  done
  [[ "$event" == "1" ]] || { echo "Fault did not hit $family" >&2; return 1; }
  cleanup
  trap cleanup EXIT
  wait_task "$id"
  adb -s "$serial" shell monkey -p "$fixture" 1 >/dev/null
  sleep 1
  adb -s "$serial" shell uiautomator dump /sdcard/jev-fixture.xml >/dev/null
  local count
  count="$(adb -s "$serial" exec-out cat /sdcard/jev-fixture.xml | grep -o "text=\"$title\"" | wc -l)"
  [[ "$count" == "1" ]] || { echo "Expected one entity named $title, got $count" >&2; return 1; }
  echo "PASS $family $id"
}

prepare_fixture
normal="$(start 'In the Jev Mobile Fixture, create an item titled FixtureNormalAcceptance with body Hello Fixture Acceptance')"
wait_task "$normal"
echo "PASS normal $normal"

prepare_fixture
faulted_task create_entity CrashCreateFixtureAcceptance CreateCrashBodyAcceptance
prepare_fixture
faulted_task text_write CrashTextFixtureAcceptance CrashTextBodyAcceptance
