-- disable account_taxcloud_tc integration
DO $$
BEGIN
    IF to_regclass('res_company') IS NULL THEN
        RETURN;
    END IF;

    IF EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_name = 'res_company'
           AND column_name = 'taxcloud_api_id_v3'
    ) THEN
        EXECUTE 'UPDATE res_company SET taxcloud_api_id_v3 = NULL';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_name = 'res_company'
           AND column_name = 'taxcloud_api_key_v3'
    ) THEN
        EXECUTE 'UPDATE res_company SET taxcloud_api_key_v3 = NULL';
    END IF;
END $$;
