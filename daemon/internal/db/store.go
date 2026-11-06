package db

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"

	_ "modernc.org/sqlite"
)

// DaemonNamespace is the standard namespace tuple ("daemon", "cadence") stored as JSON string.
var DaemonNamespace = `["daemon", "cadence"]`

const (
	DownloadsLastTriggerKey      = "downloads_last_triage_ts"
	MonthlyHygieneLastTriggerKey = "monthly_hygiene_last_run_ts"
	MemoryCleanupLastTriggerKey  = "memory_cleanup_last_run_ts"
	GamePreviousActiveKey        = "game_previous_active_name"
)

// Store provides direct access to DeskPilot's SQLite store_items table.
type Store struct {
	db *sql.DB
}

// OpenStore opens or creates the SQLite store at the given database path.
func OpenStore(dbPath string) (*Store, error) {
	if err := os.MkdirAll(filepath.Dir(dbPath), 0755); err != nil {
		return nil, fmt.Errorf("failed to create db directory: %w", err)
	}

	dsn := fmt.Sprintf("%s?_pragma=journal_mode(WAL)&_pragma=busy_timeout(5000)", dbPath)
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, fmt.Errorf("failed to open sqlite db: %w", err)
	}

	// Initialize tables if they do not exist
	initSQL := `
	CREATE TABLE IF NOT EXISTS store_items (
		namespace TEXT NOT NULL,
		key TEXT NOT NULL,
		value TEXT NOT NULL,
		created_at TEXT NOT NULL,
		updated_at TEXT NOT NULL,
		PRIMARY KEY (namespace, key)
	);
	CREATE INDEX IF NOT EXISTS idx_store_namespace ON store_items (namespace);
	`
	if _, err := db.Exec(initSQL); err != nil {
		db.Close()
		return nil, fmt.Errorf("failed to initialize tables: %w", err)
	}

	return &Store{db: db}, nil
}

// Close closes the underlying database connection.
func (s *Store) Close() error {
	if s.db != nil {
		return s.db.Close()
	}
	return nil
}

// GetItem retrieves a JSON value from the specified namespace and key.
func (s *Store) GetItem(namespace, key string) (map[string]any, error) {
	var valStr string
	query := "SELECT value FROM store_items WHERE namespace = ? AND key = ?"
	err := s.db.QueryRow(query, namespace, key).Scan(&valStr)
	if err != nil {
		if err == sql.ErrNoRows {
			return nil, nil
		}
		return nil, err
	}

	var res map[string]any
	if err := json.Unmarshal([]byte(valStr), &res); err != nil {
		return nil, fmt.Errorf("failed to unmarshal item value: %w", err)
	}
	return res, nil
}

// PutItem saves or updates a JSON value for the given namespace and key.
func (s *Store) PutItem(namespace, key string, value map[string]any) error {
	valBytes, err := json.Marshal(value)
	if err != nil {
		return fmt.Errorf("failed to marshal item value: %w", err)
	}
	valStr := string(valBytes)
	nowISO := time.Now().UTC().Format(time.RFC3339)

	upsertSQL := `
	INSERT INTO store_items (namespace, key, value, created_at, updated_at)
	VALUES (?, ?, ?, ?, ?)
	ON CONFLICT(namespace, key) DO UPDATE SET
		value = excluded.value,
		updated_at = excluded.updated_at
	`
	_, err = s.db.Exec(upsertSQL, namespace, key, valStr, nowISO, nowISO)
	return err
}

// GetLastTimestamp returns the float64 unix timestamp from a cadence key or 0 if absent.
func (s *Store) GetLastTimestamp(key string) (float64, error) {
	item, err := s.GetItem(DaemonNamespace, key)
	if err != nil {
		return 0, err
	}
	if item == nil {
		return 0, nil
	}
	if ts, ok := item["timestamp"].(float64); ok {
		return ts, nil
	}
	return 0, nil
}

// RecordTimestamp records the current timestamp into the daemon namespace.
func (s *Store) RecordTimestamp(key string) error {
	now := time.Now().UTC()
	val := map[string]any{
		"timestamp":   float64(now.Unix()),
		"recorded_at": now.Format(time.RFC3339),
	}
	return s.PutItem(DaemonNamespace, key, val)
}
