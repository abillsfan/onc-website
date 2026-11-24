<?php
/* =========================================================
   On-Cards.com form backend — shared helpers
   Used by api/subscribe.php (show alerts) and api/sell.php (sell us your cards).
   Everything submitted is saved OUTSIDE the public website folder
   (…/domains/on-cards.com/onc-private/) and emailed to ONC_NOTIFY_TO.
   ========================================================= */
if (!defined('ONC_API')) { http_response_code(404); exit; }

const ONC_NOTIFY_TO  = 'info@on-cards.com';   // where new signups / sell offers are emailed
const ONC_FROM       = 'info@on-cards.com';   // must be a mailbox on on-cards.com (Hostinger rule)
const ONC_FROM_NAME  = 'Outta Nowhere Cards';
const ONC_SITE       = 'https://on-cards.com';
const ONC_ALLOWED_HOSTS = ['on-cards.com', 'www.on-cards.com', 'localhost', '127.0.0.1'];

mb_internal_encoding('UTF-8');
date_default_timezone_set('America/Chicago');

/* ---------- where data is stored ---------- */
function onc_private_dir(): string {
  static $dir = null;
  if ($dir !== null) return $dir;
  $root = rtrim((string)($_SERVER['DOCUMENT_ROOT'] ?? ''), '/');
  $candidates = [];
  if ($root !== '') $candidates[] = dirname($root) . '/onc-private';   // outside public_html (preferred)
  $candidates[] = __DIR__ . '/.private';                                 // fallback, locked by .htaccess
  foreach ($candidates as $c) {
    if ((is_dir($c) || @mkdir($c, 0750, true)) && is_writable($c)) {
      if (strpos($c, __DIR__) === 0) onc_lock_dir($c);
      return $dir = $c;
    }
  }
  return $dir = '';
}

function onc_lock_dir(string $d): void {
  $ht = "$d/.htaccess";
  if (!file_exists($ht)) {
    @file_put_contents($ht, "<IfModule mod_authz_core.c>\n  Require all denied\n</IfModule>\n<IfModule !mod_authz_core.c>\n  Order allow,deny\n  Deny from all\n</IfModule>\n");
  }
  if (!file_exists("$d/index.html")) @file_put_contents("$d/index.html", '');
}

/* ---------- request helpers ---------- */
function onc_wants_json(): bool {
  return ($_SERVER['HTTP_X_REQUESTED_WITH'] ?? '') === 'fetch'
      || strpos((string)($_SERVER['HTTP_ACCEPT'] ?? ''), 'application/json') !== false;
}

function onc_back_url(string $fallback): string {
  $b = (string)($_POST['back'] ?? '');
  return (preg_match('#^/[A-Za-z0-9/_\-.\#]*$#', $b) && strpos($b, '//') === false) ? $b : $fallback;
}

/** Send the result: JSON for the site's script, a small page for browsers without JavaScript. */
function onc_respond(bool $ok, string $msg, string $back, int $code = 200, array $extra = []): void {
  if (!headers_sent()) {
    header('Cache-Control: no-store');
    header('X-Content-Type-Options: nosniff');
  }
  if (onc_wants_json()) {
    http_response_code($code);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode(['ok' => $ok, 'message' => $msg] + $extra);
    exit;
  }
  http_response_code($code);
  header('Content-Type: text/html; charset=utf-8');
  $title = $ok ? 'You\'re all set' : 'Hold up';
  $m = htmlspecialchars($msg, ENT_QUOTES);
  $b = htmlspecialchars($back, ENT_QUOTES);
  echo "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
     . "<meta name=\"robots\" content=\"noindex\"><title>$title | Outta Nowhere Cards</title>"
     . "<link rel=\"stylesheet\" href=\"/assets/style.css?v=20261123forms\"></head>"
     . "<body><main class=\"final\" style=\"min-height:100vh;display:grid;place-items:center\"><div class=\"wrap narrow\" style=\"text-align:center\">"
     . "<h1 class=\"slash\">" . ($ok ? 'You\'re <span>in.</span>' : 'Hold <span>up.</span>') . "</h1><p class=\"lede\">$m</p>"
     . "<div class=\"btn-row center\"><a class=\"btn btn-blue btn-lg\" href=\"$b\"><span>" . ($ok ? 'Back to On-Cards.com' : 'Go back and fix it') . "</span></a></div>"
     . "</div></main></body></html>";
  exit;
}

/** POST only, from our own site. */
function onc_guard_request(string $back): void {
  if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
    header('Allow: POST');
    onc_respond(false, 'This address only accepts form submissions.', $back, 405);
  }
  foreach (['HTTP_ORIGIN', 'HTTP_REFERER'] as $h) {
    $v = (string)($_SERVER[$h] ?? '');
    if ($v === '') continue;
    $host = strtolower((string)parse_url($v, PHP_URL_HOST));
    if ($host !== '' && !in_array($host, ONC_ALLOWED_HOSTS, true)) {
      onc_respond(false, 'Please use the form on On-Cards.com.', $back, 403);
    }
    break; // Origin wins when present
  }
}

/** Bots: hidden "website" field filled in, or form sent back faster than a person can type. */
function onc_is_bot(): bool {
  if (trim((string)($_POST['website'] ?? '')) !== '') return true;
  $t = (string)($_POST['t'] ?? '');
  if ($t !== '' && ctype_digit($t)) {
    $age = (int)(microtime(true) * 1000) - (int)$t;
    if ($age >= 0 && $age < 2500) return true;
  }
  return false;
}

function onc_client_ip(): string {
  $ip = (string)($_SERVER['HTTP_CF_CONNECTING_IP'] ?? $_SERVER['REMOTE_ADDR'] ?? '');
  return filter_var($ip, FILTER_VALIDATE_IP) ? $ip : '';
}

/**
 * Max $limit successful submissions per IP per hour for one form.
 * Call with $record=false before checking the form, and $record=true once it was accepted,
 * so typos and fix-ups don't count against anyone.
 */
function onc_rate_limited(string $bucket, int $limit, bool $record = false): bool {
  $dir = onc_private_dir();
  if ($dir === '') return false;
  $file = "$dir/ratelimit.json";
  $fh = @fopen($file, 'c+');
  if (!$fh) return false;
  flock($fh, LOCK_EX);
  $data = json_decode((string)stream_get_contents($fh), true) ?: [];
  $now = time();
  $key = $bucket . ':' . hash('sha256', onc_client_ip() . '|onc');
  foreach ($data as $k => $times) {
    $data[$k] = array_values(array_filter((array)$times, fn($ts) => $ts > $now - 3600));
    if (!$data[$k]) unset($data[$k]);
  }
  $hit = count($data[$key] ?? []) >= $limit;
  if ($record && !$hit) $data[$key][] = $now;
  ftruncate($fh, 0); rewind($fh);
  fwrite($fh, json_encode($data));
  flock($fh, LOCK_UN); fclose($fh);
  return $hit;
}

/* ---------- field cleaning ---------- */
function onc_text(string $name, int $max): string {
  $v = (string)($_POST[$name] ?? '');
  $v = str_replace(["\r\n", "\r"], "\n", $v);
  $v = preg_replace('/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/u', '', $v) ?? '';
  return mb_substr(trim($v), 0, $max);
}
function onc_line(string $name, int $max): string {
  return trim(preg_replace('/\s+/u', ' ', onc_text($name, $max)) ?? '');
}
function onc_email(string $name): string {
  $e = strtolower(onc_line($name, 120));
  return ($e !== '' && filter_var($e, FILTER_VALIDATE_EMAIL)) ? $e : ($e === '' ? '' : '!');
}
/** US phone → "662-555-1234"; '' if blank; '!' if not a valid US number. */
function onc_phone(string $name): string {
  $raw = onc_line($name, 30);
  if ($raw === '') return '';
  $d = preg_replace('/\D/', '', $raw) ?? '';
  if (strlen($d) === 11 && $d[0] === '1') $d = substr($d, 1);
  if (strlen($d) !== 10 || $d[0] < '2') return '!';
  return substr($d, 0, 3) . '-' . substr($d, 3, 3) . '-' . substr($d, 6);
}
/** Checkbox group limited to allowed values. */
function onc_pick(string $name, array $allowed): array {
  $v = $_POST[$name] ?? [];
  if (!is_array($v)) $v = [$v];
  return array_values(array_intersect($allowed, array_map('strval', $v)));
}
function onc_choice(string $name, array $allowed, string $default = ''): string {
  $v = (string)($_POST[$name] ?? '');
  return in_array($v, $allowed, true) ? $v : $default;
}

/* ---------- storage ---------- */
/** Append one row to a CSV (opens fine in Excel / Google Sheets). Returns false if it couldn't save. */
function onc_csv_append(string $file, array $header, array $row): bool {
  $dir = onc_private_dir();
  if ($dir === '') return false;
  $path = "$dir/$file";
  $new = !file_exists($path);
  $fh = @fopen($path, 'a');
  if (!$fh) return false;
  flock($fh, LOCK_EX);
  if ($new) { fwrite($fh, "\xEF\xBB\xBF"); fputcsv($fh, $header, ',', '"', ''); }
  // Stop spreadsheet formula tricks (=, +, -, @ at the start of a cell)
  $safe = array_map(fn($c) => preg_match('/^[=+\-@\t\r]/', (string)$c) ? "'" . $c : (string)$c, $row);
  $ok = fputcsv($fh, $safe, ',', '"', '') !== false;
  flock($fh, LOCK_UN); fclose($fh);
  @chmod($path, 0640);
  return $ok;
}

function onc_csv_rows(string $file): array {
  $dir = onc_private_dir();
  $path = "$dir/$file";
  if ($dir === '' || !is_readable($path)) return [];
  $rows = [];
  if ($fh = fopen($path, 'r')) {
    $head = fgetcsv($fh, null, ',', '"', '');
    if ($head) $head[0] = preg_replace('/^\xEF\xBB\xBF/', '', (string)$head[0]);
    while (($r = fgetcsv($fh, null, ',', '"', '')) !== false) {
      if ($head && count($r) === count($head)) $rows[] = array_combine($head, $r);
    }
    fclose($fh);
  }
  return $rows;
}

/* ---------- email ---------- */
function onc_header_safe(string $s): string {
  return trim(str_replace(["\r", "\n"], ' ', $s));
}

/**
 * Send a plain-text email, optionally with attachments [['name'=>..,'type'=>..,'data'=>..], ...].
 * Returns true if PHP handed it to the mail server.
 */
function onc_mail(string $to, string $subject, string $body, string $replyTo = '', array $attachments = []): bool {
  $subjectEnc = mb_encode_mimeheader(onc_header_safe($subject), 'UTF-8', 'B', "\r\n");
  $headers = [];
  $headers[] = 'From: ' . mb_encode_mimeheader(ONC_FROM_NAME, 'UTF-8', 'Q') . ' <' . ONC_FROM . '>';
  if ($replyTo !== '' && filter_var($replyTo, FILTER_VALIDATE_EMAIL)) $headers[] = 'Reply-To: ' . onc_header_safe($replyTo);
  $headers[] = 'MIME-Version: 1.0';
  $headers[] = 'X-Mailer: On-Cards.com';
  $body = str_replace(["\r\n", "\r"], "\n", $body);
  if (!$attachments) {
    $headers[] = 'Content-Type: text/plain; charset=UTF-8';
    $headers[] = 'Content-Transfer-Encoding: 8bit';
    $msg = str_replace("\n", "\r\n", $body);
  } else {
    $b = 'onc_' . bin2hex(random_bytes(12));
    $headers[] = "Content-Type: multipart/mixed; boundary=\"$b\"";
    $msg  = "--$b\r\nContent-Type: text/plain; charset=UTF-8\r\nContent-Transfer-Encoding: 8bit\r\n\r\n" . str_replace("\n", "\r\n", $body) . "\r\n";
    foreach ($attachments as $a) {
      $fn = preg_replace('/[^A-Za-z0-9._-]/', '_', (string)$a['name']);
      $msg .= "--$b\r\nContent-Type: {$a['type']}; name=\"$fn\"\r\nContent-Transfer-Encoding: base64\r\n"
            . "Content-Disposition: attachment; filename=\"$fn\"\r\n\r\n" . chunk_split(base64_encode($a['data'])) . "\r\n";
    }
    $msg .= "--$b--\r\n";
  }
  return @mail($to, $subjectEnc, $msg, implode("\r\n", $headers), '-f' . ONC_FROM);
}

function onc_log(string $line): void {
  $dir = onc_private_dir();
  if ($dir !== '') @file_put_contents("$dir/forms.log", date('Y-m-d H:i:s') . "  $line\n", FILE_APPEND | LOCK_EX);
}
