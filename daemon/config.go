package main

import (
	"os"
	"path/filepath"
	"strings"

	"gopkg.in/yaml.v3"
)

type Config struct {
	Screenshots struct {
		Directory string `yaml:"directory"`
	} `yaml:"screenshots"`
	Downloads struct {
		Directory string `yaml:"directory"`
	} `yaml:"downloads"`
	Daemon struct {
		Enabled              bool    `yaml:"enabled"`
		PollIntervalSeconds  float64 `yaml:"poll_interval_seconds"`
		ScreenshotThreshold  int     `yaml:"screenshot_threshold"`
		DownloadsCadenceDays int     `yaml:"downloads_cadence_days"`
		NotificationStyle    string  `yaml:"notification_style"` // "bento" or "toast"
	} `yaml:"daemon"`
	Storage struct {
		Enabled bool   `yaml:"enabled"`
		DbPath  string `yaml:"db_path"`
	} `yaml:"storage"`
	Memory struct {
		Enabled                         bool `yaml:"enabled"`
		PeriodicCleanupEnabled          bool `yaml:"periodic_cleanup_enabled"`
		PeriodicIntervalMinutes         int  `yaml:"periodic_interval_minutes"`
		HighMemoryLoadThresholdPercent int  `yaml:"high_memory_load_threshold_percent"`
	} `yaml:"memory"`
}

// expandHome expands a leading ~/ or ~\ to the current user's profile directory.
func expandHome(path string) string {
	if path == "" {
		return ""
	}
	if path == "~" {
		return os.Getenv("USERPROFILE")
	}
	if strings.HasPrefix(path, "~/") || strings.HasPrefix(path, "~\\") {
		userProfile := os.Getenv("USERPROFILE")
		if userProfile != "" {
			return filepath.Join(userProfile, path[2:])
		}
	}
	return path
}

// DefaultConfig returns a Config populated with sensible defaults and resolved paths.
func DefaultConfig(projectRoot string) *Config {
	var cfg Config
	cfg.Daemon.Enabled = true
	cfg.Daemon.PollIntervalSeconds = 60
	cfg.Daemon.ScreenshotThreshold = 10
	cfg.Daemon.DownloadsCadenceDays = 5
	cfg.Daemon.NotificationStyle = "bento"
	cfg.Storage.Enabled = true
	cfg.Memory.Enabled = true
	cfg.Memory.PeriodicCleanupEnabled = true
	cfg.Memory.PeriodicIntervalMinutes = 30
	cfg.Memory.HighMemoryLoadThresholdPercent = 75

	resolveDefaultPaths(&cfg, projectRoot)
	return &cfg
}

func resolveDefaultPaths(cfg *Config, projectRoot string) {
	userProfile := os.Getenv("USERPROFILE")

	cfg.Screenshots.Directory = expandHome(cfg.Screenshots.Directory)
	if cfg.Screenshots.Directory == "" && userProfile != "" {
		cfg.Screenshots.Directory = filepath.Join(userProfile, "Pictures", "Screenshots")
	}

	cfg.Downloads.Directory = expandHome(cfg.Downloads.Directory)
	if cfg.Downloads.Directory == "" && userProfile != "" {
		cfg.Downloads.Directory = filepath.Join(userProfile, "Downloads")
	}

	cfg.Storage.DbPath = expandHome(cfg.Storage.DbPath)
	if cfg.Storage.DbPath == "" {
		if userProfile != "" {
			cfg.Storage.DbPath = filepath.Join(userProfile, ".deskpilot", "storage.db")
		} else {
			cfg.Storage.DbPath = filepath.Join(projectRoot, "storage.db")
		}
	}
}

func LoadConfig(projectRoot string) (*Config, error) {
	cfgPath := filepath.Join(projectRoot, "config.yaml")
	data, err := os.ReadFile(cfgPath)
	if err != nil {
		return nil, err
	}

	cfg := DefaultConfig(projectRoot)
	// Clear default paths before unmarshaling so YAML values can override them cleanly
	cfg.Screenshots.Directory = ""
	cfg.Downloads.Directory = ""
	cfg.Storage.DbPath = ""

	if err := yaml.Unmarshal(data, cfg); err != nil {
		return nil, err
	}

	resolveDefaultPaths(cfg, projectRoot)
	return cfg, nil
}
