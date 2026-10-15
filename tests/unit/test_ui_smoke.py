import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QHeaderView, QPushButton, QTableWidgetItem
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from data90.database.base import Base
from data90.ui.home import CalendarPage
from data90.ui.window import MainWindow


def test_main_window_has_no_players_tab_but_keeps_player_entry_points() -> None:
    app = QApplication.instance() or QApplication([])
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    window = MainWindow(sessionmaker(bind=engine, expire_on_commit=False))

    assert window.windowTitle() == "Data 90"
    assert window.page_stack.count() == 6
    assert isinstance(window.calendar_page, CalendarPage)
    assert window.page_stack.currentWidget() is window.calendar_page
    home_title = window.findChild(QPushButton, "appTitle")
    assert home_title is not None
    assert home_title.text() == "Data 90"
    assert not hasattr(window, "home_button")
    assert window.page_stack.widget(5) is window.search_page
    assert not window._buttons
    assert not hasattr(window, "sidebar")
    assert not hasattr(window, "menu_toggle")
    assert window.players_page not in [
        window.page_stack.widget(index) for index in range(window.page_stack.count())
    ]
    assert not window.players_page._loaded

    standings = window.competitions_page.standings_table
    assert [standings.horizontalHeaderItem(i).text() for i in range(4)] == [
        "Pos.",
        "Equipo",
        "Pts",
        "PJ",
    ]
    points = QTableWidgetItem("18")
    window.competitions_page._style_standing_cell(points, 2, 18)
    assert points.font().bold()
    assert points.foreground().color().name() == "#e8f5fb"
    for difference, expected in ((0, "#e8edf2"), (3, "#47d49a"), (-2, "#ff7b83")):
        cell = QTableWidgetItem(str(difference))
        window.competitions_page._style_standing_cell(cell, 9, difference)
        assert cell.foreground().color().name() == expected
    for column, expected in ((7, "#47d49a"), (8, "#ff7b83")):
        cell = QTableWidgetItem("5")
        window.competitions_page._style_standing_cell(cell, column, 5)
        assert cell.foreground().color().name() == expected
    competitions_page = window.competitions_page
    competitions_page.current_has_expanded_stats = True
    competitions_page._set_tabs_for_competition("PL")
    stats_header = competitions_page.team_stats_table.horizontalHeader()
    assert all(
        stats_header.sectionResizeMode(column) == QHeaderView.ResizeMode.Fixed
        for column in range(competitions_page.team_stats_table.columnCount())
    )
    for competition_code in ("LALIGA", "UCL", "UEL", "SERIE_A"):
        competitions_page._set_tabs_for_competition(competition_code)
        assert all(
            stats_header.sectionResizeMode(column) == QHeaderView.ResizeMode.Fixed
            for column in range(competitions_page.team_stats_table.columnCount())
        )
    hidden_profile_fields = ("dorsal", "pierna", "preferred_foot", "shirt_number")
    for table in (
        window.players_page.directory_table,
        window.players_page.roster_table,
        window.teams_page.squad_table,
    ):
        headers = [
            table.horizontalHeaderItem(column).text().casefold()
            for column in range(table.columnCount())
        ]
        assert not any(field in header for field in hidden_profile_fields for header in headers)
    selected_players: list[int] = []
    window.players_page.open_player_detail = selected_players.append
    window.select_page(2)
    window.teams_page.player_selected.emit(42)
    assert selected_players == [42]
    assert window.page_stack.currentIndex() == 2

    squad_player = QTableWidgetItem("Lisandro Martínez")
    squad_player.setData(Qt.ItemDataRole.UserRole, 43)
    window.teams_page.squad_table.setRowCount(1)
    window.teams_page.squad_table.setItem(0, 0, squad_player)
    window.teams_page.squad_table.itemClicked.emit(squad_player)
    assert selected_players == [42, 43]

    window.select_page(5)
    window.search_page.player_selected.emit(7)
    assert selected_players == [42, 43, 7]
    assert window.page_stack.currentIndex() == 5
    opened_competitions: list[str] = []
    window.competitions_page.open_detail = opened_competitions.append
    window.calendar_page.competition_selected.emit("PL")
    assert opened_competitions == ["PL"]
    assert window.page_stack.currentIndex() == 1
    home_title.click()
    assert window.page_stack.currentWidget() is window.calendar_page
    for page_index in range(window.page_stack.count()):
        window.select_page(page_index)
        assert window.page_stack.currentIndex() == page_index

    window.resize(1500, 900)
    window.select_page(1)
    competitions_page.current_code = "PL"
    competitions_page.current_has_expanded_stats = True
    competitions_page.stack.setCurrentWidget(competitions_page.detail_page)
    competitions_page._set_tabs_for_competition("PL")
    competitions_page.tabs.setCurrentWidget(competitions_page.team_stats_page)
    window.show()
    app.processEvents()
    competitions_page._fit_expanded_team_stats_columns()
    app.processEvents()
    assert sum(
        stats_header.sectionSize(column)
        for column in range(competitions_page.team_stats_table.columnCount())
    ) == competitions_page.team_stats_table.viewport().width()
    assert competitions_page.team_stats_table.columnWidth(3) >= 56
    assert competitions_page.team_stats_table.columnWidth(10) >= 90
    first_team_width = competitions_page.team_stats_table.columnWidth(0)
    window.resize(1200, 900)
    app.processEvents()
    competitions_page._fit_expanded_team_stats_columns()
    app.processEvents()
    assert competitions_page.team_stats_table.columnWidth(0) < first_team_width
    assert sum(
        stats_header.sectionSize(column)
        for column in range(competitions_page.team_stats_table.columnCount())
    ) == competitions_page.team_stats_table.viewport().width()

    window.close()
    window.deleteLater()
    app.processEvents()
    engine.dispose()
