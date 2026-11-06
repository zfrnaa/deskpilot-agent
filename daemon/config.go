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

func LoadConfig(projectRoot string) (*Config, error) {
	cfgPath := filepath.Join(projectRoot, "config.yaml")
	data, err := os.ReadFile(cfgPath)
	if err != nil {
		return nil, err
	}

	var cfg Config
	// Defaults
	cfg.Daemon.Enabled = true
	cfg.Daemon.PollIntervalSeconds = 60
	cfg.Daemon.ScreenshotThreshold = 10
	cfg.Daemon.DownloadsCadenceDays = 5
	cfg.Daemon.NotificationStyle = "bento"

	if err := yaml.Unmarshal(data, &cfg); err != nil {
		return nil, err
	}

	// Expand ~/ and resolve defaults
	cfg.Screenshots.Directory = expandHome(cfg.Screenshots.Directory)
	if cfg.Screenshots.Directory == "" {
		userProfile := os.Getenv("USERPROFILE")
		if userProfile != "" {
			cfg.Screenshots.Directory = filepath.Join(userProfile, "Pictures", "Screenshots")
		}
	}

	cfg.Storage.DbPath = expandHome(cfg.Storage.DbPath)
	if cfg.Storage.DbPath == "" {
		userProfile := os.Getenv("USERPROFILE")
		if userProfile != "" {
			cfg.Storage.DbPath = filepath.Join(userProfile, ".deskpilot", "storage.db")
		} else {
			cfg.Storage.DbPath = filepath.Join(projectRoot, "storage.db")
		}
	}

	return &cfg, nil
}
