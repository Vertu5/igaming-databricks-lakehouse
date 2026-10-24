-- Bronze: raw ingestion of the CSVs, no business transformation.
-- Parameters (set by the job): catalog, schema, sample_mod.
-- sample_mod keeps players with player_id % sample_mod = 0, applied to all three tables so that
-- referential integrity holds. 1 = full data (prod), 20 = 5 percent sample (dev, runs in seconds).
USE CATALOG IDENTIFIER(:catalog);
CREATE SCHEMA IF NOT EXISTS IDENTIFIER(:schema);
USE SCHEMA IDENTIFIER(:schema);

-- Raw landing zone shared by all environments.
CREATE OR REPLACE TABLE bronze_players AS
SELECT * FROM read_files('/Volumes/workspace/igaming/raw/players.csv', format => 'csv', header => true, inferSchema => true)
WHERE player_id % CAST(:sample_mod AS INT) = 0;

CREATE OR REPLACE TABLE bronze_sessions AS
SELECT * FROM read_files('/Volumes/workspace/igaming/raw/sessions.csv', format => 'csv', header => true, inferSchema => true)
WHERE player_id % CAST(:sample_mod AS INT) = 0;

CREATE OR REPLACE TABLE bronze_spins AS
SELECT * FROM read_files('/Volumes/workspace/igaming/raw/spins.csv', format => 'csv', header => true, inferSchema => true)
WHERE player_id % CAST(:sample_mod AS INT) = 0;
