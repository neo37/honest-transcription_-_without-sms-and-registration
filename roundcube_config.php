<?php
/* Local configuration for Roundcube Webmail */
$config['db_dsnw'] = 'sqlite:////data/roundcube/roundcube.db';
$config['imap_host'] = '%n';
$config['imap_conn_options'] = array(
    'ssl' => array(
        'verify_peer'       => false,
        'verify_peer_name'  => false
    ),
);
$config['smtp_conn_options'] = array(
    'ssl'         => array(
        'verify_peer'      => false,
        'verify_peer_name' => false,
        'allow_self_signed' => true
    ),
);

$config['smtp_user'] = '%u';
$config['smtp_pass'] = '%p';
$config['smtp_auth_type'] = 'LOGIN';
$config['smtp_host'] = 'tls://mail2.business-pad.com:587';
$config['smtp_log'] = true;

$config['support_url'] = '../../../../admin/install/instructions';
$config['log_dir'] = '/data/var/log';
$config['temp_dir'] = '/tmp';
$config['des_key'] = hex2bin('208dc56580a593bb197f4d9969b7e3e44f761f4f9f1e0134');

if (is_file('/data/server.ini')) {
    $data = parse_ini_file('/data/server.ini');
}

$config['plugins'] = array(
    'archive',
    'attachment_reminder',
    'emoticons',
    'enigma',
    'identity_select',
    'newmail_notifier',
    'managesieve',
    'vcard_attachments',
    'zipdownload',
    'dovecot_ident',
    'poste_passkey_login',
);

$config['language'] = '';
$config['skin'] = 'elastic';
$config['session_lifetime'] = 300;
