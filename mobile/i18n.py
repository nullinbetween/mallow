"""
Two languages, one product.

Mallow was written in Traditional Chinese because that is the language of the
person it was built for. It is being shown to judges who read English. Those are
not two products and they must not become two templates: the moment there are
two copies of the meadow, one of them starts drifting and only one of them gets
tested.

So: one template, one set of routes, one pipeline, and a table of strings.
Everything a person reads comes through `t()`. Everything a person *said* does
not — `source_text` and `transcript` are shown exactly as recorded and are never
translated. `activity_text` is different on purpose: it is Mallow's short,
canonical English computation label, not a quotation from the person.
A record made in English and the same record made in Chinese take the identical
path through extraction, policy and the ledger; only the furniture around them
changes.

How the language is picked, first match wins:

    ?lang=en | ?lang=zh-Hant   an explicit choice, and it is remembered
    the mallow_lang cookie      that earlier choice
    Accept-Language             what the browser asked for
    zh-Hant                     the default

The submission demo uses `?lang=en` explicitly rather than relying on the
judge's browser headers.
"""
from __future__ import annotations

from typing import Optional

from flask import request

# 🔴 English, not Chinese. Owner, 2026-08-29.
#
# This is the last stop in `from_request()`: an explicit `?lang=`, then the
# remembered cookie, then Accept-Language, and only then this. So it is not
# "the app's language" — it is what somebody gets when nothing about them says
# otherwise. A judge on a Japanese or German browser matched none of the
# earlier rules and was handed Traditional Chinese.
#
# It is also the fallback inside `t()` when an entry is missing a language, and
# the rules require the application to support English at a minimum. Both point
# the same way.
#
# 🚫 This does not make Mallow an English app. Chinese is still reached by the
# browser sending zh, by the switch on the page, and by `?lang=zh-Hant`.
DEFAULT = "en"
LANGS = ("zh-Hant", "en")
COOKIE = "mallow_lang"

# The tag that goes in <html lang="…">.
HTML_LANG = {"zh-Hant": "zh-Hant", "en": "en"}

STRINGS: dict[str, dict[str, str]] = {
    # ---------------------------------------------------------- the meadow --
    "app_name":        {"zh-Hant": "Mallow", "en": "Mallow"},
    "hold_label":      {"zh-Hant": "按住兔子說話，放開就記下來",
                        "en": "Hold the rabbit and speak. Let go when you are done."},
    "hold_help":       {"zh-Hant": "用鍵盤的話，按空白鍵開始，再按一次結束。",
                        "en": "With a keyboard: press space to start, and again to stop."},
    "entry":           {"zh-Hant": "說點什麼…", "en": "Say something…"},
    "note_label":      {"zh-Hant": "說一句話", "en": "Say one thing"},
    "note_placeholder": {"zh-Hant": "今天做了什麼？", "en": "What happened today?"},
    "cancel":          {"zh-Hant": "取消", "en": "Cancel"},
    # 🔴 Shown inside the settings panel when nobody has chosen a workspace yet.
    # It is the honest version of a saved confirmation: the choice is kept, but
    # it is kept on this device and it is not anybody's setting yet.
    "settings_pending": {"zh-Hant": "還沒有空間可以存。你選好之後，這些設定會跟著你。",
                         "en": "There is nowhere to save this yet. It will follow "
                               "you once you choose a workspace."},
    "settings_kept":   {"zh-Hant": "先記在這台裝置上了。",
                        "en": "Kept on this device for now."},
    # 🔴 Two states the panel used to collapse into one.
    #
    # "there is nowhere to save this yet" (above) is true of somebody who has
    # not chosen a workspace. It is NOT true of somebody who has one and whose
    # page is still resolving it - for them the product defaults are a claim
    # about their schedule, and the claim is wrong. So that window gets its own
    # sentence, and the panel shows no values at all while it lasts.
    "settings_loading": {"zh-Hant": "正在讀取你存的設定…",
                         "en": "Loading your saved settings…"},
    # 🔴 Read failed. The panel deliberately shows nothing rather than falling
    # back to defaults: a default on screen is one Save away from replacing a
    # schedule the person actually chose.
    "settings_load_failed": {"zh-Hant": "讀不到你存的設定。這裡先不顯示任何值，以免蓋掉你原本存的。",
                             "en": "Could not load your saved settings. Nothing is "
                                   "shown here, so nothing can overwrite what you saved."},
    # 🔴 The way out of the waiting screen. Short, and not styled as a warning:
    # changing your mind is ordinary, and the person is already tired.
    "stop_waiting":    {"zh-Hant": "取消", "en": "Cancel"},
    # 🔴 Three sentences, and the difference between them is the whole point.
    #
    #   stopped_waiting   provisional. The screen is free, and Mallow has not
    #                     yet heard back about the data. Says nothing it does
    #                     not know.
    #   discarded_note    definitive, and only after the server has confirmed.
    #   discard_unconfirmed  the honest ending when it could not confirm. It
    #                     does not apologise and it does not pretend.
    "stopped_waiting": {"zh-Hant": "好，停下來了。", "en": "All right, stopped."},
    # The store is append-only, so a committed row may remain internally as a
    # cancelled audit fact even though every ordinary product read excludes it.
    # Say what the person needs to know — it will not be kept as one of their
    # records — without making the stronger and sometimes false claim that no
    # write ever happened.
    "discarded_note":  {"zh-Hant": "好，這次不留。",
                        "en": "All right, this one will not be kept."},
    "discard_unconfirmed": {"zh-Hant": "已經停下來了，但我沒能確認這次有沒有被記下。之後可以在紀錄頁看一下。",
                            "en": "Stopped — but Mallow could not confirm whether this "
                                  "one was recorded. You can check the records page."},
    "discard_receipt": {"zh-Hant": "不要這筆", "en": "Don't keep this"},
    "discarding_note": {"zh-Hant": "好，正在替你拿掉這次記錄。",
                         "en": "All right — removing this one."},
    "receipt_saved_reply_failed": {"zh-Hant": "已經收好了，只是兔子的回應暫時沒有顯示出來。",
                                   "en": "It was kept, but the rabbit's reply did not appear."},
    # After 「再說一次」: the keyboard is gone and the rabbit is waiting.
    "say_again_hint":  {"zh-Hant": "按住兔子，再說一次。",
                        "en": "Hold the rabbit and say it again."},
    # 🔴 Q-20. Shown the instant the finger lands, before anything async runs.
    # `getUserMedia` takes one to two seconds on a phone, and until Q-20 that
    # was one to two seconds of nothing at all: the Owner could not tell the
    # press had registered, let go, and the recording never began.
    #
    # It has to say *keep holding*, not "please wait" — the failure mode was a
    # finger coming off, so this line's job is an instruction. The warmth is
    # the Owner's: the press is the most intimate moment in the product, and a
    # purely functional line wastes it. Short, because it lives for one second.
    "hold_on":         {"zh-Hant": "手指暖暖的，按著就好",
                        "en": "Warm — keep holding"},
    # Shown only once the microphone is actually running. Never before: a
    # prompt that says "listening" while the permission sheet is still open
    # would be the app pretending to hear.
    "listening_hint":  {"zh-Hant": "在聽了，說吧", "en": "Listening — go ahead"},
    # 🔴 Deliberately not "listening". By the time this shows, the microphone
    # is closed: saying "listening" would invite someone to keep talking into a
    # microphone that stopped recording. The ear is for what was already heard.
    "taking_it_in":    {"zh-Hant": "聽進去了", "en": "Taking it in"},
    # Shown when the microphone was granted but the press had already ended —
    # which on iOS is *every first attempt*, because the permission sheet
    # appears over the page and the finger comes off it. Doing nothing silently
    # meant the first try always failed with no explanation.
    "mic_ready":       {"zh-Hant": "好了，再按一次就可以說話",
                        "en": "Ready — press again and speak"},
    # The gap between "sent" and "answered" is two to four seconds of a real
    # model call. Without this the page looks frozen.
    "thinking":        {"zh-Hant": "讓我想一下", "en": "Thinking"},
    # People open the meadow and do not know what a sentence is supposed to
    # look like. Two examples, deliberately of the two kinds that earn
    # different food, and deliberately not about anyone real.
    "note_examples":   {"zh-Hant": "例如：幫全部衣服寫名字，三十五分鐘 · "
                                   "一直記著要預約牙醫",
                        "en": "For example: labelled all the clothes, 35 minutes · "
                              "kept remembering to book the dentist"},
    "send":            {"zh-Hant": "好了", "en": "Done"},
    "edit":            {"zh-Hant": "再說一次", "en": "Say it again"},
    "book":            {"zh-Hant": "紀錄", "en": "Records"},
    "leaf_open":       {"zh-Hant": "打開摺好的葉子", "en": "Open the folded leaf"},
    "card_close":      {"zh-Hant": "收起來", "en": "Put it away"},
    "settings_open":   {"zh-Hant": "設定", "en": "Settings"},
    "settings_title":  {"zh-Hant": "小回顧", "en": "Quiet reflections"},
    "reflection_cadence": {"zh-Hant": "多久回顧一次", "en": "How often"},
    "cadence_off":     {"zh-Hant": "關閉", "en": "Off"},
    "cadence_daily":   {"zh-Hant": "每天", "en": "Daily"},
    "cadence_weekly":  {"zh-Hant": "每週", "en": "Weekly"},
    "cadence_biweekly": {"zh-Hant": "每兩週", "en": "Every two weeks"},
    "cadence_monthly": {"zh-Hant": "每月", "en": "Monthly"},
    "reflection_time": {"zh-Hant": "想在幾點收到", "en": "Preferred time"},
    "reflection_weekday": {"zh-Hant": "星期幾", "en": "Day of week"},
    "reflection_monthday": {"zh-Hant": "每月幾號", "en": "Day of month"},
    "weekday_mon": {"zh-Hant": "星期一", "en": "Monday"},
    "weekday_tue": {"zh-Hant": "星期二", "en": "Tuesday"},
    "weekday_wed": {"zh-Hant": "星期三", "en": "Wednesday"},
    "weekday_thu": {"zh-Hant": "星期四", "en": "Thursday"},
    "weekday_fri": {"zh-Hant": "星期五", "en": "Friday"},
    "weekday_sat": {"zh-Hant": "星期六", "en": "Saturday"},
    "weekday_sun": {"zh-Hant": "星期日", "en": "Sunday"},
    "reflection_note": {"zh-Hant": "到了你選的時間，Mallow 只整理這段期間的新紀錄。沒有新紀錄就安靜不打擾。",
                         "en": "At the time you choose, Mallow only reflects on new records from that period. No new records means silence."},
    "settings_save":   {"zh-Hant": "存好", "en": "Save"},
    "settings_close":  {"zh-Hant": "取消", "en": "Cancel"},
    "settings_saved":  {"zh-Hant": "回顧時間存好了。", "en": "Reflection time saved."},
    "demo_pill":       {"zh-Hant": "DEMO · 示範資料", "en": "DEMO · sample data"},
    "temp_pill":       {"zh-Hant": "暫時試玩空間", "en": "Temporary workspace"},
    "temp_pill_local": {"zh-Hant": "這個工作空間只存在這台裝置的這個瀏覽器裡。清掉瀏覽器資料就會不見。",
                        "en": "This workspace exists only in this browser on this "
                              "machine. Clearing site data loses it."},
    "temp_pill_anon":  {"zh-Hant": "暫時試玩空間。清除瀏覽器資料後可能無法取回。",
                        "en": "A temporary workspace. Clearing site data can lose it."},

    # ------------------------------------------------ signing in, and why ---
    #
    # 🔴 Every one of these exists because the front door used to say nothing.
    # A sign-in that failed left the gate exactly as it was, so the only thing
    # the person could report was "I pressed it and nothing happened" — which
    # is what happened for a week. Each failure now has words.
    "auth_popup_blocked": {
        "zh-Hant": "瀏覽器把登入視窗擋掉了。允許這個網站開啟視窗後再按一次就可以。",
        "en": "The browser blocked the sign-in window. Allow pop-ups for this "
              "site and press it again."},
    "auth_popup_closed": {
        "zh-Hant": "登入視窗關掉了，還沒有完成。想繼續的話再按一次。",
        "en": "The sign-in window closed before it finished. Press it again "
              "whenever you want to."},
    "auth_failed": {
        "zh-Hant": "登入沒有完成。再試一次；如果一直這樣，先用「先看看就好」也可以。",
        "en": "Sign-in did not complete. Try again — or start with “Just look "
              "around” for now."},
    "auth_not_ready": {
        "zh-Hant": "還在準備登入，一下就好。",
        "en": "Getting sign-in ready — one moment."},
    # 🔴 Google succeeded and Mallow did not. Saying "sign-in failed" here
    # would be a lie: the identity has already changed, and on the anonymous
    # path the link is permanent. What is missing is only this app's session.
    "auth_session_failed": {
        "zh-Hant": "Google 那一步完成了，但 Mallow 還沒接上。你的登入沒有白費 —— "
                   "按一下「重試連接」就好。",
        "en": "Google is done, but Mallow has not connected yet. Your sign-in "
              "is not lost — press “Try connecting again”."},
    "auth_connecting":  {"zh-Hant": "連接中…", "en": "Connecting…"},
    "auth_retry":       {"zh-Hant": "重試連接", "en": "Try connecting again"},
    "auth_signout_failed": {
        "zh-Hant": "還沒能確認已登出。頁面會留在這裡，請再試一次。",
        "en": "Mallow could not confirm sign-out. This page will stay here; please try again."},

    # ----------------------------------------------------------- the gate ---
    "gate_lede":       {"zh-Hant": "先選一種方式開始", "en": "Pick a way to start"},
    "sign_in_google":  {"zh-Hant": "用 Google 登入", "en": "Sign in with Google"},
    "just_look":       {"zh-Hant": "先看看就好", "en": "Just look around"},
    "gate_note":       {"zh-Hant": "「先看看就好」是暫時空間，換裝置或清除瀏覽器資料就不在了。",
                        "en": "“Just look around” is a temporary workspace: it "
                              "does not follow you to another device, and clearing "
                              "site data loses it."},

    # ------------------------------------------- the account, in Settings ---
    #
    # An anonymous workspace has one clear exit. Google sign-in is offered at
    # the front door only; no account-linking flow is exposed from inside it.
    "account_title":   {"zh-Hant": "帳號", "en": "Account"},
    "account_leave":   {"zh-Hant": "退出匿名模式", "en": "Exit anonymous mode"},
    "account_google_body":  {"zh-Hant": "已用 Google 登入。紀錄跟著這個帳號。",
                             "en": "Signed in with Google. Your records follow "
                                   "this account."},
    "account_signed_as": {
        "zh-Hant": "已使用 {email} 登入。紀錄跟著這個帳號。",
        "en": "Signed in as {email}. Your records follow this account."},
    "account_signout":      {"zh-Hant": "登出", "en": "Sign out"},
    # ------------------------------------------------- what the rabbit says --
    "line_both":       {"zh-Hant": "收好了。今天有草，也有蘿蔔。",
                        "en": "Kept. There is grass today, and a carrot."},
    "line_grass":      {"zh-Hant": "收好了。今天有一把草。",
                        "en": "Kept. A handful of grass today."},
    "line_carrot":     {"zh-Hant": "收好了。有一根蘿蔔。",
                        "en": "Kept. One carrot."},
    "line_plain":      {"zh-Hant": "收好了。", "en": "Kept."},
    # 🔴 Two situations that used to share one sentence, and the shared
    # sentence was the wrong one for both.
    #
    # "這個我沒聽出什麼要記的" told the person that what they said was not
    # worth recording. For a product whose whole subject is work nobody sees,
    # that is the injury it exists to name. Ruled out by the Owner and the
    # Strategic Officer on 2026-08-23, along with every phrasing of the shape
    # "nothing worth recording" — see `test_the_rabbit_never_says_there_was_
    # nothing_worth_recording`.
    #
    #   heard_nothing      no transcript at all. The ear failed, not the person.
    #   heard_no_activity  a transcript, but nothing that maps to an activity.
    #                      Mallow says what it did — and that it heard them.
    # 🔴 2026-08-25, Owner's ruling after testing silence on a real phone.
    #
    # It used to say 「我沒有聽清楚」/ "I couldn't hear that clearly". That was
    # written to keep the blame on the rabbit's ear rather than on the person,
    # and that intent stands. But it is not true of the case it most often
    # meets: somebody who deliberately said nothing. The ear heard perfectly;
    # there was simply nothing. "Not clearly" implies there was something.
    #
    # 🔴 The app cannot tell "you said nothing" from "the microphone missed
    # you" — on an energy meter those are the same reading. So one sentence has
    # to cover both, and it must be true in both. "No sound reached me" is
    # exactly what the gate measured, and it still does not say the person
    # failed to say anything worth keeping — which is the injury this whole
    # group of strings exists to avoid.
    "heard_nothing":   {"zh-Hant": "這次我沒有收到聲音。可以再說一次，或改用打字告訴我。",
                        "en": "No sound reached me this time. Try again, "
                              "or type it instead."},
    "heard_no_activity": {"zh-Hant": "我聽到了。這次沒有新增活動紀錄——不過，我聽到了。",
                          "en": "I heard you. No activity was added this time—but "
                                "I heard you."},
    "line_no_time":    {"zh-Hant": "收好了。沒有提供時長也沒關係。",
                        "en": "Kept. It is fine that no duration was given."},
    "line_clock_time": {"zh-Hant": "收好了，{time} 也記下了。",
                        "en": "Kept. I noted {time} too."},
    "line_time_description": {"zh-Hant": "收好了，提到的時間也記下了。",
                              "en": "Kept. I noted the time you mentioned too."},
    "line_unsure":     {"zh-Hant": "我先照聽到的收好了。需要的話，可以不要這筆或再說一次。",
                         "en": "I kept what I heard. You can remove it or say it again if needed."},
    # 🔴 Always shown after a capture, never only when the model doubts itself.
    "you_said":        {"zh-Hant": "你說：", "en": "You said: "},
    "line_heard":      {"zh-Hant": "我聽到你{heard}。", "en": "I heard: {heard}. "},
    "cannot_record":   {"zh-Hant": "現在記不下來，等一下再說一次好嗎？",
                        "en": "I cannot write that down just now. Try again in a moment?"},

    # -------------------------------------------------------- record page ---
    "records_title":   {"zh-Hant": "Mallow · 紀錄", "en": "Mallow · Records"},
    "records_h1":      {"zh-Hant": "你的紀錄", "en": "Your records"},
    # 🔴 No arrow character here. The button draws its own SVG arrow, and the
    # string used to carry a second one: real device QA showed "← ← 回草原".
    "back":            {"zh-Hant": "回草原", "en": "Back to the meadow"},

    # ── the corner clock (Q-38) ──────────────────────────────────────────────
    #
    # `locale_tag` is what `Intl.DateTimeFormat` is given, so the date reads the
    # way the reader writes dates — 8月29日（金） and Fri, 29 Aug are the same
    # fact in two hands. It is a BCP-47 tag, not a translated string; do not
    # "translate" it into something friendlier.
    "locale_tag":      {"zh-Hant": "zh-Hant", "en": "en-GB"},
    # 🔴 The box is 64px wide. "Sat, 29 Aug" does not fit centred inside it;
    # two short lines do. Weekday over day-and-month.
    # 🔴 Shape of the compact date, not the date itself — the formatter still
    # produces every word. A 76px square holds two short parts and no more:
    #     8/29 · 六        Fri · 29 Aug
    # Chinese reads the number first and abbreviates the weekday to one
    # character; English leads with the weekday and needs the month spelt.
    "clock_weekday":   {"zh-Hant": "narrow", "en": "short"},
    "clock_month":     {"zh-Hant": "numeric", "en": "short"},
    "clock_order":     {"zh-Hant": "date_first", "en": "weekday_first"},
    # 🔴 Says out loud what the clock is, because a number in a corner could be
    # read as the time of something that was recorded. It is not. It is now.
    "clock_label":     {"zh-Hant": "現在時間 {time}。點一下可以收起。",
                        "en": "The time now is {time}. Tap to put it away."},

    # ── What the three things in the meadow are ──────────────────────────────
    #
    # Real device QA, 2026-08-27: "大家都不知道草和蘿蔔和葉子是什麼". The names
    # are the product's own vocabulary and nobody arrives knowing them. The
    # explanation is behind a mark rather than on the page because somebody who
    # already knows should not have to read it every time.
    #
    # 🔴 `legend_note` is not padding. Three numbers in a row is the shape of a
    # scoreboard, and this product's rule is that there is no score. The panel
    # that explains them is the one place that can say so.
    "legend_open":     {"zh-Hant": "這些是什麼？", "en": "What are these?"},
    "legend_title":    {"zh-Hant": "草、蘿蔔、葉子是什麼",
                        "en": "What grass, carrots and leaves are"},
    # 🔴 The definition sentence of each of the three is the 戰略官's approved
    # wording (2026-08-27). The examples after it are the deputy's and are
    # additive: a judge opening this page has never met the vocabulary, and an
    # abstract definition of "照顧與準備" does not tell them what to say to a
    # rabbit. The leaf keeps 「不需要你開 app」 because that sentence is the
    # product's whole claim about the autonomous loop.
    "legend_grass":    {"zh-Hant": "🌿 草 —— 花了實際時間、卻常沒有被算進去的照顧與準備。"
                                   "貼名字、補快用完的東西、收拾、哄睡、陪孩子上學。",
                        "en": "🌿 Grass — care and preparation that took real "
                              "time and usually is not counted: labelling, "
                              "restocking before it runs out, tidying, settling "
                              "a child, taking them to school."},
    "legend_carrot":   {"zh-Hant": "🥕 蘿蔔 —— 記住、安排、追蹤與協調等 mental load。"
                                   "做這些的時候，手上看起來什麼都沒發生。",
                        "en": "🥕 Carrot — the mental load of remembering, "
                              "arranging, tracking and coordinating. While you "
                              "carry it, nothing looks like it is happening."},
    "legend_leaf":     {"zh-Hant": "🍃 葉子 —— Mallow 根據近期紀錄自動準備的私人小回顧。"
                                   "到了你設定的時間它自己會寫，不需要你開 app。",
                        "en": "🍃 Leaf — a short private review Mallow prepares "
                              "on its own from your recent records. It writes "
                              "at the time you chose; you do not have to open "
                              "the app."},
    "legend_note":     {"zh-Hant": "🚫 這些是紀錄，不是分數或目標。數量少不代表你做得少，"
                                   "只反映這段時間你告訴 Mallow 的內容。",
                        "en": "🚫 These are records, not a score or a target. A "
                              "smaller number does not mean you did less — it "
                              "only reflects what you told Mallow in that time."},
    "legend_close":    {"zh-Hant": "知道了", "en": "Got it"},
    "demo_banner":     {"zh-Hant": "DEMO · 示範資料，與真實紀錄分開存放",
                        "en": "DEMO · sample data, stored apart from real records"},
    "anon_heading":    {"zh-Hant": "這是暫時的試玩空間。", "en": "This is a temporary workspace."},
    "anon_body":       {"zh-Hant": "這些紀錄屬於目前的暫時空間，不會跟著你到另一個裝置。"
                                   "清除瀏覽器資料或離開這個空間後，可能就取不回來了。",
                        "en": "These records belong to this temporary workspace. "
                              "They do not follow you to another device, and clearing "
                              "site data or leaving this workspace can make them inaccessible."},
    "signed_in":       {"zh-Hant": "已登入。", "en": "Signed in."},
    "sign_out":        {"zh-Hant": "登出", "en": "Sign out"},
    "cross_device_yes": {"zh-Hant": "使用 Google 登入後，可以在其他裝置取回紀錄。",
                         "en": "Signed in with Google, your records come back on "
                               "another device."},
    "cross_device_no": {"zh-Hant": "目前是本機測試 workspace，不提供跨裝置保存。跨裝置取回要等 Firestore 接通並測試過才會開始。",
                        "en": "This is a local test workspace and does not keep "
                              "records across devices. Cross-device recovery begins "
                              "once Firestore is wired and tested."},
    "records_lede":    {"zh-Hant": "這裡是你自己說過的話，以及 Mallow 依此記下的內容。"
                                   "每一列都標明哪些是你說的、哪些是 Mallow 的判讀。"
                                   "匯出是把它複製成一般格式的一份個人副本，你想給誰、要不要給，都由你決定。"
                                   "Mallow 不會自動分享，也不會寄給任何人。",
                        "en": "These are your own words, and what Mallow filed from "
                              "them. Every row marks which part you said and which "
                              "part is Mallow's reading. An export is a copy in an "
                              "ordinary format; who sees it, and whether anyone does, "
                              "is yours to decide. Mallow shares nothing "
                              "automatically and sends nothing to anyone."},
    "meadow_totals":   {"zh-Hant": "草原裡的照顧", "en": "Care in the meadow"},
    "total_grass":     {"zh-Hant": "草 {n}", "en": "Grass {n}"},
    "total_carrot":    {"zh-Hant": "蘿蔔 {n}", "en": "Carrots {n}"},
    "total_leaves":    {"zh-Hant": "葉子 {n}", "en": "Leaves {n}"},
    "totals_note":     {"zh-Hant": "葉子收起後仍會留在這裡；這些是紀錄，不是目標或分數。",
                        "en": "Put-away leaves remain here. These are records, "
                              "not goals or scores."},
    "download_pdf":    {"zh-Hant": "下載 PDF", "en": "Download PDF"},
    "download_csv":    {"zh-Hant": "下載 CSV", "en": "Download CSV"},
    "empty":           {"zh-Hant": "還沒有紀錄。回草原對兔子說一句話就會出現。",
                        "en": "Nothing recorded yet. Say one thing to the rabbit and "
                              "it will appear here."},
    "remove_capture":  {"zh-Hant": "移除這次記錄", "en": "Remove this entry"},
    "remove_title":    {"zh-Hant": "移除這次記錄？", "en": "Remove this entry?"},
    "remove_body":     {"zh-Hant": "這次說的整段內容，以及由它整理出的活動，都不會再出現在一般紀錄或回顧裡。",
                         "en": "This whole capture and the activities filed from it will "
                               "no longer appear in your records or reflections."},
    "remove_keep":     {"zh-Hant": "保留", "en": "Keep it"},
    "remove_confirm":  {"zh-Hant": "移除", "en": "Remove"},
    "remove_working":  {"zh-Hant": "正在移除…", "en": "Removing…"},
    "remove_failed":   {"zh-Hant": "還沒能確認已移除。這次記錄仍然保留，請稍後再試。",
                         "en": "Mallow could not confirm removal. This entry is still "
                               "shown; please try again later."},
    "food_grass":      {"zh-Hant": "🌿 草", "en": "🌿 grass"},
    "food_carrot":     {"zh-Hant": "🥕 蘿蔔", "en": "🥕 carrot"},
    "food_none":       {"zh-Hant": "已被算作工作", "en": "already counted as work"},
    "food_withheld":   {"zh-Hant": "未判定", "en": "not classified"},
    "minutes_said":    {"zh-Hant": "{n} 分鐘（你說的）", "en": "{n} minutes (you said)"},
    "no_minutes":      {"zh-Hant": "未提供時長",
                        "en": "no duration (none needed)"},
    "occurred_time":   {"zh-Hant": "發生時間：{time}", "en": "Occurred at: {time}"},
    "time_description": {"zh-Hant": "時間描述：{time}", "en": "Time noted: {time}"},
    "recorded_time":   {"zh-Hant": "記錄時間：{time}", "en": "Recorded at: {time}"},
    "supersedes":      {"zh-Hant": "取代 {id}", "en": "replaces {id}"},
    "claim":           {"zh-Hant": "Append-only by application policy, with traceable corrections.",
                        "en": "Append-only by application policy, with traceable corrections."},
    # 🔴 Reworded 2026-08-29, in BOTH languages. The old sentence told the
    # reader that the previous row is kept beside the new one, which invited
    # them to look for it — and since that day's ruling no default read face
    # shows it. Storage has not changed; the sentence now describes what the
    # person is actually given.
    #
    # 🔴 The Chinese was left on the old wording for one round because the edit
    # that changed it was written and never saved, and nothing asserted the two
    # languages agreed. `test_the_two_languages_make_the_same_claim` exists so
    # that cannot happen twice.
    "claim_note":      {"zh-Hant": "修改不會覆寫歷史：紀錄頁與預設匯出顯示的是目前有效的版本。",
                        "en": "A change never overwrites history: the records page and "
                              "the default export show the version that is current."},
    "audio_note":      {"zh-Hant": "Raw audio is processed in memory and is not persisted by Mallow.",
                        "en": "Raw audio is processed in memory and is not persisted by Mallow."},
    "nature":          {"zh-Hant": "這是使用者自行陳述的活動紀錄，供回顧與自願分享。",
                        "en": "This is a structured self-reported activity record, for "
                              "personal reflection and optional sharing."},
    "where_firestore": {"zh-Hant": "資料存放在專案的 Firestore；專案管理員與獲授權的服務帳號在技術上仍可能存取，Mallow 對此據實說明。",
                        "en": "Records are stored in the project's Firestore. Project "
                              "administrators and authorised service accounts can "
                              "technically reach them, and Mallow says so rather than "
                              "claiming otherwise."},
    "where_local":     {"zh-Hant": "資料目前存在執行這個服務的機器上，一個 uid 一份，尚未接上 Firestore。能存取那台機器的人在技術上仍可能存取，Mallow 對此據實說明。",
                        "en": "Records are currently kept on the machine running this "
                              "service, one workspace per uid, and Firestore is not "
                              "wired yet. Anyone with access to that machine can "
                              "technically reach them, and Mallow says so rather than "
                              "claiming otherwise."},

    # -------------------------------------------------------------- leaf ----
    "leaf_title":      {"zh-Hant": "一片摺好的葉子", "en": "A folded leaf"},

    # -------------------------------------------------------------- pdf -----
    "pdf_title":       {"zh-Hant": "Mallow · 活動紀錄", "en": "Mallow · Activity record"},
    "pdf_sub":         {"zh-Hant": "使用者自行陳述的活動紀錄，供回顧與自願分享。",
                        "en": "A structured self-reported activity record, for personal "
                              "reflection and optional sharing."},
    "pdf_empty":       {"zh-Hant": "這份紀錄目前是空的。", "en": "This record is empty."},
    "pdf_minutes":     {"zh-Hant": "{n} 分鐘（使用者自述）", "en": "{n} minutes (self-reported)"},
    "pdf_no_time":     {"zh-Hant": "未提供時長", "en": "no duration given"},
    "pdf_occurred_time": {"zh-Hant": "發生時間：{time}", "en": "Occurred at: {time}"},
    "pdf_time_description": {"zh-Hant": "時間描述：{time}",
                             "en": "Time noted: {time}"},
    "pdf_recorded_time": {"zh-Hant": "記錄時間：{time}",
                          "en": "Recorded at: {time}"},
    # Words, not emoji: the embedded face carries no pictographs, so a leaf or
    # a carrot glyph would be a blank box on one reader and nothing on another.
    "pdf_food_grass":    {"zh-Hant": "草", "en": "grass"},
    "pdf_food_carrot":   {"zh-Hant": "蘿蔔", "en": "carrot"},
    "pdf_food_none":     {"zh-Hant": "已被算作工作", "en": "already counted as work"},
    "pdf_food_withheld": {"zh-Hant": "未判定", "en": "not classified"},

    # ------------------------------------------------------ language switch --
    "switch_to":       {"zh-Hant": "EN", "en": "中文"},
    "switch_label":    {"zh-Hant": "Switch to English", "en": "切換為繁體中文"},
}


def normalise(tag: Optional[str]) -> Optional[str]:
    if not tag:
        return None
    low = tag.strip().lower()
    if low.startswith("en"):
        return "en"
    if low.startswith("zh"):
        return "zh-Hant"
    return None


def from_request() -> str:
    """Explicit choice, then the remembered one, then the browser, then default."""
    chosen = normalise(request.args.get("lang"))
    if chosen:
        return chosen
    remembered = normalise(request.cookies.get(COOKIE))
    if remembered:
        return remembered
    header = request.headers.get("Accept-Language", "")
    for part in header.split(","):
        found = normalise(part.split(";")[0])
        if found:
            return found
    return DEFAULT


def t(key: str, lang: str = DEFAULT, **fmt) -> str:
    """
    One string. A missing key raises rather than rendering its own name.

    A template that shows `records_h1` to a judge is worse than one that fails
    a test, so the failure is moved to where the tests are.
    """
    entry = STRINGS.get(key)
    if entry is None:
        raise KeyError(f"no string named {key!r}")
    text = entry.get(lang) or entry[DEFAULT]
    return text.format(**fmt) if fmt else text


def other(lang: str) -> str:
    return "zh-Hant" if lang == "en" else "en"


def bundle(lang: str, keys: tuple[str, ...]) -> dict[str, str]:
    """The subset the page's own script needs, handed over as JSON."""
    return {k: t(k, lang) for k in keys}


# The strings the browser script uses. Kept explicit so a template cannot ship
# the whole table — including wording only the server should ever compose.
SCRIPT_KEYS = ("cannot_record", "line_plain", "you_said",
               "settings_pending", "settings_kept",
               "stop_waiting", "stopped_waiting", "discarded_note",
               "discard_unconfirmed", "discard_receipt", "discarding_note",
               "receipt_saved_reply_failed", "say_again_hint",
               "heard_nothing", "listening_hint", "thinking",
               "taking_it_in", "mic_ready", "hold_on",
               "temp_pill_local", "temp_pill_anon", "settings_saved",
               "auth_popup_blocked", "auth_popup_closed", "auth_failed",
               "auth_not_ready", "auth_session_failed", "auth_connecting",
               "auth_retry", "auth_signout_failed", "account_signed_as",
               "remove_working", "remove_failed",
               "locale_tag", "clock_label",
               "clock_weekday", "clock_month", "clock_order")
