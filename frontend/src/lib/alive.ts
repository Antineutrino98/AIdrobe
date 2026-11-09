/**
 * 已废弃（U4 裁决 2）。
 *
 * 旧版靠前端心跳 + /client-gone 让 launcher 看门狗杀掉后端。
 * 桌面客户端化后，退出唯一主责归 pywebview 的 window.events.closed，
 * 由 launcher 的幂等 shutdown() 优雅关库；这里不再需要任何心跳。
 * 文件保留仅为说明，不要再从 App 里引入。
 */
