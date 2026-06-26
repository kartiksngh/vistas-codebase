@echo off
cd /d "%~dp0"
echo ================================================================
echo   Vistas - ENCRYPTED off-machine backup of arm_repo\
echo   (the LICENSED LSEG StarMine ARM dump - it can NEVER go to
echo    GitHub, so it is encrypted locally and mirrored to a cloud
echo    drive; the cloud only ever stores ciphertext.)
echo.
echo     (no args)        = incremental encrypted backup
echo     --verify         = check the mirror is complete + decryptable
echo     --restore DEST   = rebuild an arm_repo tree from the mirror
echo.
echo   Target : VISTAS_ARM_BACKUP_DIR, else ^<OneDrive^>\VistasBackups\arm_repo
echo   Key    : VISTAS_ARM_BACKUP_PASSPHRASE (recommended; kept in your
echo            password manager), else ~/.vistas/arm_backup.key
echo            *** KEEP THE KEY OFF THIS MACHINE or the backup is
echo                unrecoverable after a disk crash. ***
echo ================================================================
echo.
python -m vistas.arm_backup %*
echo.
pause
