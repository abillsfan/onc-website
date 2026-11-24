<?php
/* =========================================================
   Automatic show-alert emails.
   The Cloudflare hook (cloudflare/worker.js) calls this every 5 min on weekends and hourly noon-11 PM CT on weekdays. Each call:
     1. asks Whatnot for ONC's scheduled and live shows
     2. a show we haven't seen scheduled  -> "New show" email to the list
        a show we haven't seen live       -> "We're LIVE" email to the list
     3. sends up to SEND_PER_RUN emails; anything left goes out on the next call
   The very first run only records the shows that already exist, so nobody gets old news.
   State: onc-private/alerts-state.json. Calling it more often does nothing extra (throttled).
   ========================================================= */
define('ONC_API', true);
require __DIR__ . '/_lib.php';

const CHECK_EVERY_SECONDS = 40;
const SEND_PER_RUN = 40;             // stays well under Hostinger's sending limits
const SKIP_SCHEDULED_IF_WITHIN = 20; // minutes: a show starting this soon only gets the "live" email
const DROP_LIVE_AFTER_HOURS = 4;     // don't keep sending "we're live" for a show that's long over
const SEND_RECEIPTS = true;          // email info@on-cards.com when an alert finishes sending

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
header('X-Robots-Tag: noindex');
ignore_user_abort(true);
set_time_limit(120);

function done(array $a): void { echo json_encode($a, JSON_UNESCAPED_SLASHES); exit; }

$dir = onc_private_dir();
if ($dir === '') done(['ok' => false, 'error' => 'storage not writable']);
$fh = fopen("$dir/alerts-state.json", 'c+');
if (!$fh || !flock($fh, LOCK_EX | LOCK_NB)) done(['ok' => true, 'skipped' => 'already running']);
$state = json_decode((string)stream_get_contents($fh), true) ?: [];
$state += ['seeded' => false, 'sched' => [], 'live' => [], 'queue' => [], 'history' => []];
$now = time();

$save = function () use ($fh, &$state) {
  ftruncate($fh, 0); rewind($fh);
  fwrite($fh, json_encode($state, JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT));
  fflush($fh); flock($fh, LOCK_UN); fclose($fh);
};

/* ---------- 1. check Whatnot (throttled) ---------- */
$checked = false;
if ($now - (int)($state['last_check'] ?? 0) >= CHECK_EVERY_SECONDS || isset($_GET['force_check'])) {
  $checked = true;
  $state['last_check'] = $now;
  [$c1, $sch, $e1] = onc_whatnot_get('/shows', ['status' => 'scheduled', 'limit' => 50]);
  [$c2, $liv, $e2] = $e1 === '' ? onc_whatnot_get('/shows', ['status' => 'live', 'limit' => 10]) : [0, null, ''];
  $err = $e1 ?: $e2;
  $state['last_ok'] = $err === '';
  $state['last_error'] = $err === '' ? null : $err . ' (' . date('M j g:i A', $now) . ')';
  if ($err === '') {
    $public = fn($list) => array_values(array_filter((array)($list['data'] ?? []), fn($s) => !empty($s['id']) && ($s['visibility'] ?? 'public') === 'public'));
    $scheduled = $public($sch);
    $live = $public($liv);

    if (!$state['seeded']) {
      foreach ($scheduled as $s) $state['sched'][$s['id']] = $now;
      foreach ($live as $s) { $state['live'][$s['id']] = $now; $state['sched'][$s['id']] = $now; }
      $state['seeded'] = true;
      $state['history'][] = date('M j g:i A', $now) . ': alerts switched on (' . count($scheduled) . ' scheduled show(s) already on the calendar, no emails sent for those)';
      onc_log('show-alerts seeded');
    } else {
      $recipients = null;
      $enqueue = function (string $type, array $s) use (&$state, &$recipients, $now) {
        $recipients ??= onc_alert_recipients();
        $state['queue'][] = [
          'type' => $type, 'id' => $s['id'], 'title' => trim((string)($s['title'] ?? 'Live show')),
          'start' => (string)($s['scheduled_start_at'] ?? ''), 'queued' => $now,
          'to' => array_map(fn($e, $n) => [$e, $n], array_keys($recipients), array_values($recipients)), 'sent' => 0, 'failed' => 0,
        ];
      };
      foreach ($live as $s) {
        if (isset($state['live'][$s['id']])) continue;
        $state['live'][$s['id']] = $now;
        $state['sched'][$s['id']] ??= $now;
        // the "new show" email for this show is old news now
        $state['queue'] = array_values(array_filter($state['queue'], fn($j) => !($j['type'] === 'scheduled' && $j['id'] === $s['id'])));
        $enqueue('live', $s);
      }
      foreach ($scheduled as $s) {
        if (isset($state['sched'][$s['id']])) continue;
        $state['sched'][$s['id']] = $now;
        $start = strtotime((string)($s['scheduled_start_at'] ?? '')) ?: 0;
        if ($start > $now + SKIP_SCHEDULED_IF_WITHIN * 60) $enqueue('scheduled', $s);
      }
    }
    // forget shows older than 60 days
    foreach (['sched', 'live'] as $k) $state[$k] = array_filter($state[$k], fn($t) => $t > $now - 60 * 86400);
  }
}

/* ---------- 2. send queued emails ---------- */
function when_text(string $iso): string {
  $t = strtotime($iso);
  if (!$t) return '';
  $d = new DateTime('@' . $t);
  $d->setTimezone(new DateTimeZone('America/Chicago'));
  return $d->format('l, M j') . ' at ' . $d->format('g:i A') . ' CT';
}

function alert_email(array $job, string $email, string $first): array {
  $link = 'https://www.whatnot.com/live/' . rawurlencode($job['id']);
  $hi = $first !== '' ? "Hey $first," : 'Hey,';
  $title = $job['title'];
  if ($job['type'] === 'live') {
    $subject = "🔴 We're LIVE on Whatnot: $title";
    $body = "$hi\n\nWe just went live on Whatnot!\n\n$title\n\nJump in now: $link\n\nSee you in the chat.\n- Outta Nowhere Cards";
  } else {
    $when = when_text($job['start']);
    $subject = "New show: $title" . ($when ? " — $when" : '');
    $body = "$hi\n\nNew show on the calendar:\n\n$title\n" . ($when ? "$when\n" : '')
          . "\nSee the show and set a reminder: $link\n\nWe'll email you again the second we go live.\n- Outta Nowhere Cards";
  }
  return [$subject, $body . onc_list_footer($email)];
}

$sent = 0;
// live alerts first
usort($state['queue'], fn($a, $b) => ($a['type'] === 'live' ? 0 : 1) <=> ($b['type'] === 'live' ? 0 : 1) ?: $a['queued'] <=> $b['queued']);
$gone = onc_unsubscribed();
foreach ($state['queue'] as &$job) {
  if ($job['type'] === 'live' && $now - $job['queued'] > DROP_LIVE_AFTER_HOURS * 3600) { $job['to'] = []; $job['dropped'] = true; }
  if ($job['type'] === 'scheduled' && ($st = strtotime($job['start'])) && $st < $now) { $job['to'] = []; $job['dropped'] = true; }
  while ($job['to'] && $sent < SEND_PER_RUN) {
    [$email, $first] = array_shift($job['to']);
    if (isset($gone[strtolower($email)])) continue;
    [$subject, $body] = alert_email($job, $email, (string)$first);
    $ok = onc_mail($email, $subject, $body, '', [], onc_list_headers($email));
    $ok ? $job['sent']++ : $job['failed']++;
    $sent++;
  }
  if (!$job['to']) {
    $label = $job['type'] === 'live' ? "We're LIVE" : 'New show';
    $line = date('M j g:i A', $now) . ": \"$label\" email for \"{$job['title']}\" sent to {$job['sent']}"
          . ($job['failed'] ? " ({$job['failed']} failed)" : '') . (!empty($job['dropped']) ? ' (rest skipped, show already started/ended)' : '');
    $state['history'][] = $line;
    onc_log("show-alerts $line");
    if (SEND_RECEIPTS && ($job['sent'] || $job['failed'])) {
      onc_mail(ONC_NOTIFY_TO, "Show alert sent: $label — {$job['title']}",
        "Your automatic show alert went out.\n\n$line\n\nShow: https://www.whatnot.com/live/{$job['id']}\n");
    }
    $job['finished'] = true;
  }
}
unset($job);
$state['queue'] = array_values(array_filter($state['queue'], fn($j) => empty($j['finished'])));
$state['history'] = array_slice($state['history'], -30);
$save();

done(['ok' => (bool)($state['last_ok'] ?? false), 'checked' => $checked, 'sent' => $sent,
      'waiting' => array_sum(array_map(fn($j) => count($j['to']), $state['queue'])), 'error' => $state['last_error'] ?? null]);
