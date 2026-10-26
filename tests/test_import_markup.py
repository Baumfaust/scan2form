from scan2form.spike.import_markup import merge_stroke_pieces

PX_PER_MM = 4.0


def test_touching_pieces_of_one_stroke_are_merged():
    pieces = [(0, 100, 30, 4, 60), (31, 102, 40, 4, 80), (72, 99, 20, 5, 50)]
    assert merge_stroke_pieces(pieces, PX_PER_MM) == [(0, 99, 92, 7, 190)]


def test_separate_fields_on_one_row_stay_separate():
    postcode, town = (0, 100, 60, 4, 120), (60 + 20, 100, 200, 4, 400)  # 5 mm apart
    assert sorted(merge_stroke_pieces([town, postcode], PX_PER_MM)) == [postcode, town]


def test_strokes_on_different_rows_and_outlines_are_untouched():
    row1, row2 = (0, 100, 50, 4, 100), (0, 140, 50, 4, 100)  # 10 mm apart vertically
    outline = (55, 90, 80, 40, 300)
    assert sorted(merge_stroke_pieces([row1, row2, outline], PX_PER_MM)) == [row1, row2, outline]
