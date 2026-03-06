<?php
$config["smtp_host"] = "tls://mail2.business-pad.com:587";
$config["smtp_user"] = "%u";
$config["smtp_pass"] = "%p";
$config["smtp_auth_type"] = "LOGIN";
$config["smtp_conn_options"] = array(
    "ssl" => array(
        "verify_peer" => false,
        "verify_peer_name" => false,
        "allow_self_signed" => true,
    ),
);
$config["smtp_log"] = true;
?>
