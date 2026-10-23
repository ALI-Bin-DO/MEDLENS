-- Read-only MySQL user for MedLens: can SELECT from medlens, nothing else.
-- BEFORE RUNNING: replace CHANGE_ME_READONLY_PASSWORD (both places) with your own password
-- (and put the same one in MYSQL_READONLY_PASSWORD in your .env).

CREATE USER IF NOT EXISTS 'medlens_readonly'@'localhost' IDENTIFIED BY 'CHANGE_ME_READONLY_PASSWORD';

-- Re-running resets the password to whatever is written above.
ALTER USER 'medlens_readonly'@'localhost' IDENTIFIED BY 'CHANGE_ME_READONLY_PASSWORD';

GRANT SELECT ON medlens.* TO 'medlens_readonly'@'localhost';

-- Check: should list only USAGE and SELECT ON `medlens`.*
SHOW GRANTS FOR 'medlens_readonly'@'localhost';
