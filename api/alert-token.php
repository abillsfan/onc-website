<?php
/* Hands the website the Whatnot key for automatic show emails, without the key ever being in this public repo.
   GET  -> this server's public encryption key + show-alert status (nothing private).
   POST -> the key, encrypted by the "Show alerts: hand Whatnot key to website" GitHub workflow with the
           public key above. Only this server can unlock it. It's checked against Whatnot (must be
           outta_nowhere_cards) before it's saved to onc-private/whatnot-token.txt. */
define('ONC_API', true);
require __DIR__ . '/_lib.php';

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
header('X-Robots-Tag: noindex');

function out(array $a, int $code = 200): void { http_response_code($code); echo json_encode($a, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES); exit; }

$dir = onc_private_dir();
if ($dir === '') out(['ok' => false, 'error' => 'website storage folder is not writable'], 500);
if (!function_exists('openssl_pkey_new')) out(['ok' => false, 'error' => 'PHP openssl is not available on this server'], 500);

$privFile = "$dir/alert-key.pem";
if (!is_readable($privFile)) {
  $k = openssl_pkey_new(['private_key_bits' => 2048, 'private_key_type' => OPENSSL_KEYTYPE_RSA]);
  openssl_pkey_export($k, $pem);
  file_put_contents($privFile, $pem, LOCK_EX);
  @chmod($privFile, 0600);
}
$priv = openssl_pkey_get_private((string)file_get_contents($privFile));
$pub = openssl_pkey_get_details($priv)['key'];

if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
  $st = onc_json_state('alerts-state.json');
  $tok = onc_json_state('whatnot-token-info.json');
  out([
    'public_key' => $pub,
    'whatnot_key_saved' => onc_whatnot_token() !== '',
    'whatnot_account' => $tok['account'] ?? null,
    'whatnot_key_saved_at' => $tok['saved_at'] ?? null,
    'shows_readable' => $tok['shows_ok'] ?? null,
    'alerts_started' => !empty($st['seeded']),
    'last_check' => isset($st['last_check']) ? date('Y-m-d H:i:s', (int)$st['last_check']) . ' CT' : null,
    'last_check_ok' => $st['last_ok'] ?? null,
    'last_error' => $st['last_error'] ?? null,
    'emails_waiting' => array_sum(array_map(fn($j) => count($j['to'] ?? []), $st['queue'] ?? [])),
    'recent_alerts' => array_slice($st['history'] ?? [], -5),
  ]);
}

// POST: {"key": b64(RSA-OAEP(aesKey)), "iv": b64, "tag": b64, "data": b64(AES-256-GCM(token))}
$in = json_decode((string)file_get_contents('php://input'), true);
if (!is_array($in) || !isset($in['key'], $in['iv'], $in['tag'], $in['data'])) out(['ok' => false, 'error' => 'bad request'], 400);
if (!openssl_private_decrypt((string)base64_decode($in['key']), $aes, $priv, OPENSSL_PKCS1_OAEP_PADDING) || strlen($aes) !== 32) {
  out(['ok' => false, 'error' => 'could not unlock (was it encrypted with this server\'s current key?)'], 400);
}
$token = openssl_decrypt((string)base64_decode($in['data']), 'aes-256-gcm', $aes, OPENSSL_RAW_DATA, (string)base64_decode($in['iv']), (string)base64_decode($in['tag']));
$token = trim((string)$token);
if ($token === '' || strlen($token) > 4096) out(['ok' => false, 'error' => 'could not unlock'], 400);

[$code, $me, $err] = onc_whatnot_get('/users/me', [], $token);
$user = strtolower((string)($me['username'] ?? ''));
if ($err !== '' || $user !== ONC_WHATNOT_USER) {
  onc_log('alert-token rejected: ' . ($err ?: "account $user"));
  out(['ok' => false, 'error' => $err !== '' ? "Whatnot didn't accept the key ($err)" : "key belongs to '$user', not " . ONC_WHATNOT_USER], 400);
}
[$c2, $shows, $err2] = onc_whatnot_get('/shows', ['status' => 'scheduled', 'limit' => 1], $token);

file_put_contents("$dir/whatnot-token.txt", $token, LOCK_EX);
@chmod("$dir/whatnot-token.txt", 0600);
file_put_contents("$dir/whatnot-token-info.json", json_encode([
  'account' => $user, 'saved_at' => date('Y-m-d H:i:s') . ' CT', 'shows_ok' => $err2 === '', 'shows_error' => $err2,
]), LOCK_EX);
onc_log("alert-token saved account=$user shows_ok=" . ($err2 === '' ? 'y' : 'n') . ($err2 ? " ($err2)" : ''));
out(['ok' => $err2 === '', 'account' => $user, 'shows_readable' => $err2 === '',
     'error' => $err2 === '' ? null : "Key saved, but it can't read shows ($err2). Make a Whatnot key that includes the shows permission."], $err2 === '' ? 200 : 422);
